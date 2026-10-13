package collector

import (
	"bufio"
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// The spool is an optional write-ahead log for accepted batches, enabled by
// COLLECTOR_SPOOL_DIR. Every admitted batch is written and fsynced before it is forwarded.
// A batch the API answers, with success or a permanent rejection, is removed, because the
// exporter hears that answer. A batch the API could not answer (a transport failure, a 5xx
// or a 429) stays and is replayed in the background, across restarts, until the API stores
// or permanently rejects it; the exporter is told 202 so it does not also retry. A batch
// rejected during replay moves to rejected/ for the operator, since no exporter is waiting
// to hear it. The API treats a repeated span as an idempotent replay, so delivery is at
// least once without duplicate traces. When the spool is full, new batches are refused with
// 503 and the exporter keeps them; nothing is dropped silently.

const spoolMagic = "LPSPOOL1\n"

var (
	errSpoolFull    = errors.New("spool capacity reached")
	errSpoolCorrupt = errors.New("spool entry is corrupt")
)

type spoolHeader struct {
	ContentType string    `json:"content_type"`
	SHA256      string    `json:"sha256"`
	Size        int       `json:"size"`
	ReceivedAt  time.Time `json:"received_at"`
}

type spoolEntry struct {
	id       string
	size     int64 // bytes on disk, counted against the capacity
	attempts int
	due      time.Time
}

type spool struct {
	dir      string
	capacity int64

	mu       sync.Mutex
	used     int64                  // pending and rejected bytes on disk
	inflight map[string]*spoolEntry // written, and still owned by a waiting request
	replay   map[string]*spoolEntry // waiting for the background replayer
	rejected int
	wake     chan struct{}
}

type spoolStats struct {
	pending, rejected int
	bytes, capacity   int64
	oldest            time.Duration
}

func openSpool(dir string, capacity int64) (*spool, error) {
	if !filepath.IsAbs(dir) {
		return nil, errors.New("COLLECTOR_SPOOL_DIR must be an absolute path")
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, fmt.Errorf("COLLECTOR_SPOOL_DIR could not be created: %w", err)
	}
	if err := checkPrivateDirectory(dir); err != nil {
		return nil, err
	}
	s := &spool{
		dir:      dir,
		capacity: capacity,
		inflight: map[string]*spoolEntry{},
		replay:   map[string]*spoolEntry{},
		wake:     make(chan struct{}, 1),
	}
	for _, name := range []string{"pending", "rejected", "tmp"} {
		if err := os.MkdirAll(filepath.Join(dir, name), 0o700); err != nil {
			return nil, fmt.Errorf("spool directory %s could not be created: %w", name, err)
		}
	}
	// A write interrupted by a crash never reached pending/, so its exporter was never
	// answered with success and still holds the batch.
	partial, err := os.ReadDir(filepath.Join(dir, "tmp"))
	if err != nil {
		return nil, err
	}
	for _, entry := range partial {
		if err := os.Remove(filepath.Join(dir, "tmp", entry.Name())); err != nil {
			return nil, err
		}
	}
	for _, name := range []string{"pending", "rejected"} {
		entries, err := os.ReadDir(filepath.Join(dir, name))
		if err != nil {
			return nil, err
		}
		for _, entry := range entries {
			info, err := entry.Info()
			if err != nil || !info.Mode().IsRegular() || !strings.HasSuffix(entry.Name(), ".batch") {
				return nil, fmt.Errorf("unexpected entry in spool directory %s: %s", name, entry.Name())
			}
			s.used += info.Size()
			if name == "rejected" {
				s.rejected++
				continue
			}
			// Batches left by an earlier process are replayed at once.
			id := strings.TrimSuffix(entry.Name(), ".batch")
			s.replay[id] = &spoolEntry{id: id, size: info.Size()}
		}
	}
	return s, nil
}

func (s *spool) path(state, id string) string {
	return filepath.Join(s.dir, state, id+".batch")
}

// put writes a batch durably and returns its id; the caller's request owns it until it is
// released or handed to the replayer.
func (s *spool) put(contentType string, payload []byte, received time.Time) (string, error) {
	digest := sha256.Sum256(payload)
	header, err := json.Marshal(spoolHeader{
		ContentType: contentType,
		SHA256:      hex.EncodeToString(digest[:]),
		Size:        len(payload),
		ReceivedAt:  received.UTC(),
	})
	if err != nil {
		return "", err
	}
	record := make([]byte, 0, len(spoolMagic)+len(header)+1+len(payload))
	record = append(append(append(append(record, spoolMagic...), header...), '\n'), payload...)
	size := int64(len(record))

	s.mu.Lock()
	if s.used+size > s.capacity {
		s.mu.Unlock()
		return "", errSpoolFull
	}
	s.used += size
	s.mu.Unlock()

	suffix := make([]byte, 8)
	if _, err := rand.Read(suffix); err != nil {
		s.unreserve(size)
		return "", err
	}
	id := fmt.Sprintf("%020d-%s", received.UnixNano(), hex.EncodeToString(suffix))
	if err := s.writeDurably(id, record); err != nil {
		s.unreserve(size)
		return "", err
	}
	s.mu.Lock()
	s.inflight[id] = &spoolEntry{id: id, size: size}
	s.mu.Unlock()
	return id, nil
}

func (s *spool) writeDurably(id string, record []byte) error {
	temporary := filepath.Join(s.dir, "tmp", id)
	file, err := os.OpenFile(temporary, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	_, writeErr := file.Write(record)
	syncErr := file.Sync()
	closeErr := file.Close()
	if err := errors.Join(writeErr, syncErr, closeErr); err != nil {
		_ = os.Remove(temporary)
		return err
	}
	if err := os.Rename(temporary, s.path("pending", id)); err != nil {
		_ = os.Remove(temporary)
		return err
	}
	return syncDirectory(filepath.Join(s.dir, "pending"))
}

func (s *spool) unreserve(size int64) {
	s.mu.Lock()
	s.used -= size
	s.mu.Unlock()
}

// release removes a batch whose outcome its exporter has heard.
func (s *spool) release(id string) {
	s.mu.Lock()
	entry := s.inflight[id]
	if entry == nil {
		entry = s.replay[id]
	}
	delete(s.inflight, id)
	delete(s.replay, id)
	s.mu.Unlock()
	if entry == nil {
		return
	}
	if err := os.Remove(s.path("pending", id)); err == nil || errors.Is(err, os.ErrNotExist) {
		_ = syncDirectory(filepath.Join(s.dir, "pending"))
		s.unreserve(entry.size)
	}
}

// handOff passes a batch from its request to the replayer after a failed first attempt.
func (s *spool) handOff(id string, retryAfter time.Duration, now time.Time) {
	s.mu.Lock()
	entry := s.inflight[id]
	delete(s.inflight, id)
	if entry != nil {
		entry.attempts = 1
		entry.due = now.Add(retryAfter)
		s.replay[id] = entry
	}
	s.mu.Unlock()
	s.signal()
}

// reject moves a batch the API refused during replay where an operator can inspect it.
func (s *spool) reject(id string) error {
	s.mu.Lock()
	delete(s.replay, id)
	s.rejected++
	s.mu.Unlock()
	if err := os.Rename(s.path("pending", id), s.path("rejected", id)); err != nil {
		return err
	}
	return errors.Join(
		syncDirectory(filepath.Join(s.dir, "pending")),
		syncDirectory(filepath.Join(s.dir, "rejected")),
	)
}

func (s *spool) retryLater(id string, wait time.Duration, now time.Time) {
	s.mu.Lock()
	if entry := s.replay[id]; entry != nil {
		entry.attempts++
		entry.due = now.Add(wait)
	}
	s.mu.Unlock()
}

// next returns the oldest batch due for replay, or how long until one is.
func (s *spool) next(now time.Time) (*spoolEntry, time.Duration) {
	s.mu.Lock()
	defer s.mu.Unlock()
	var chosen *spoolEntry
	wait := time.Duration(-1)
	for _, entry := range s.replay {
		if !entry.due.After(now) {
			if chosen == nil || entry.id < chosen.id {
				chosen = entry
			}
			continue
		}
		if until := entry.due.Sub(now); wait < 0 || until < wait {
			wait = until
		}
	}
	if chosen != nil {
		entry := *chosen
		return &entry, 0
	}
	return nil, wait
}

func (s *spool) signal() {
	select {
	case s.wake <- struct{}{}:
	default:
	}
}

// load reads a batch and checks it against its recorded digest and size.
func (s *spool) load(id string) (string, []byte, error) {
	file, err := os.Open(s.path("pending", id))
	if err != nil {
		return "", nil, err
	}
	defer func() { _ = file.Close() }()
	reader := bufio.NewReader(file)
	magic := make([]byte, len(spoolMagic))
	if _, err := io.ReadFull(reader, magic); err != nil || string(magic) != spoolMagic {
		return "", nil, errSpoolCorrupt
	}
	line, err := reader.ReadBytes('\n')
	if err != nil {
		return "", nil, errSpoolCorrupt
	}
	var header spoolHeader
	if json.Unmarshal(bytes.TrimSuffix(line, []byte("\n")), &header) != nil ||
		(header.ContentType != "application/json" && header.ContentType != protobufMediaType) {
		return "", nil, errSpoolCorrupt
	}
	payload, err := io.ReadAll(reader)
	digest := sha256.Sum256(payload)
	if err != nil || len(payload) != header.Size || hex.EncodeToString(digest[:]) != header.SHA256 {
		return "", nil, errSpoolCorrupt
	}
	return header.ContentType, payload, nil
}

func (s *spool) stats(now time.Time) spoolStats {
	s.mu.Lock()
	defer s.mu.Unlock()
	stats := spoolStats{
		pending:  len(s.inflight) + len(s.replay),
		rejected: s.rejected,
		bytes:    s.used,
		capacity: s.capacity,
	}
	oldest := ""
	for _, group := range []map[string]*spoolEntry{s.inflight, s.replay} {
		for id := range group {
			if oldest == "" || id < oldest {
				oldest = id
			}
		}
	}
	var nanos int64
	if _, err := fmt.Sscanf(oldest, "%020d-", &nanos); err == nil {
		stats.oldest = now.Sub(time.Unix(0, nanos))
	}
	return stats
}

func syncDirectory(path string) error {
	directory, err := os.Open(path)
	if err != nil {
		return err
	}
	defer func() { _ = directory.Close() }()
	return directory.Sync()
}
