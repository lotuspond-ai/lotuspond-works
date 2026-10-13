package collector

import (
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	ListenAddress  string
	IngestToken    string
	MetricsToken   string
	APIKey         string
	ProjectID      string
	UpstreamURL    *url.URL
	MaxBodyBytes   int64
	QueueCapacity  int
	Workers        int
	RequestTimeout time.Duration
	// SpoolDir enables the durable write-ahead spool; empty keeps the collector stateless.
	SpoolDir      string
	SpoolMaxBytes int64
	// replayBackoff replaces the replay schedule in tests.
	replayBackoff func(attempt int) time.Duration
}

func ConfigFromEnv() (Config, error) {
	projectID := os.Getenv("LOTUSPOND_PROJECT_ID")
	if !regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`).MatchString(projectID) {
		return Config{}, errors.New("LOTUSPOND_PROJECT_ID must be a UUID")
	}
	base, err := parseUpstream(os.Getenv("LOTUSPOND_API_URL"), projectID)
	if err != nil {
		return Config{}, err
	}
	config := Config{
		ListenAddress: envOr("COLLECTOR_LISTEN_ADDRESS", ":4318"),
		IngestToken:   os.Getenv("COLLECTOR_INGEST_TOKEN"),
		MetricsToken:  os.Getenv("COLLECTOR_METRICS_TOKEN"),
		APIKey:        os.Getenv("LOTUSPOND_API_KEY"),
		ProjectID:     projectID,
		UpstreamURL:   base,
	}
	if len(config.IngestToken) < 24 || len(config.APIKey) < 24 {
		return Config{}, errors.New("COLLECTOR_INGEST_TOKEN and LOTUSPOND_API_KEY must each contain at least 24 characters")
	}
	if config.MetricsToken != "" {
		if len(config.MetricsToken) < 32 || len(config.MetricsToken) > 1024 ||
			!regexp.MustCompile(`^[A-Za-z0-9_-]+$`).MatchString(config.MetricsToken) {
			return Config{}, errors.New("COLLECTOR_METRICS_TOKEN must contain 32 to 1024 URL-safe random characters")
		}
		if config.MetricsToken == config.IngestToken || config.MetricsToken == config.APIKey {
			return Config{}, errors.New("COLLECTOR_METRICS_TOKEN must be distinct from ingest and upstream credentials")
		}
	}
	if _, _, err := net.SplitHostPort(config.ListenAddress); err != nil {
		return Config{}, fmt.Errorf("COLLECTOR_LISTEN_ADDRESS must be host:port: %w", err)
	}
	if config.MaxBodyBytes, err = int64Env("COLLECTOR_MAX_BODY_BYTES", 2<<20, 1024, 2<<20); err != nil {
		return Config{}, err
	}
	if config.QueueCapacity, err = intEnv("COLLECTOR_QUEUE_CAPACITY", 128, 1, 4096); err != nil {
		return Config{}, err
	}
	if config.Workers, err = intEnv("COLLECTOR_WORKERS", 4, 1, 64); err != nil {
		return Config{}, err
	}
	timeoutMillis, err := intEnv("COLLECTOR_REQUEST_TIMEOUT_MS", 5000, 100, 30000)
	if err != nil {
		return Config{}, err
	}
	config.RequestTimeout = time.Duration(timeoutMillis) * time.Millisecond
	if config.SpoolDir = os.Getenv("COLLECTOR_SPOOL_DIR"); config.SpoolDir != "" {
		if !filepath.IsAbs(config.SpoolDir) {
			return Config{}, errors.New("COLLECTOR_SPOOL_DIR must be an absolute path")
		}
		// At least two full batches, so the spool can always hold what it admits.
		if config.SpoolMaxBytes, err = int64Env("COLLECTOR_SPOOL_MAX_BYTES", 256<<20, 2*config.MaxBodyBytes+(64<<10), 1<<40); err != nil {
			return Config{}, err
		}
	}
	return config, nil
}

func parseUpstream(raw string, projectID string) (*url.URL, error) {
	if raw == "" {
		return nil, errors.New("LOTUSPOND_API_URL is required")
	}
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return nil, errors.New("LOTUSPOND_API_URL must be an absolute URL without credentials, query, or fragment")
	}
	if u.Scheme != "https" && !(u.Scheme == "http" && isLoopback(u.Hostname())) {
		return nil, errors.New("LOTUSPOND_API_URL must use HTTPS; HTTP is permitted only for a loopback address")
	}
	u.Path = strings.TrimRight(u.Path, "/") + "/api/v1/telemetry/otlp"
	query := u.Query()
	query.Set("project_id", projectID)
	u.RawQuery = query.Encode()
	return u, nil
}

func isLoopback(host string) bool {
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func envOr(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func intEnv(name string, fallback, minimum, maximum int) (int, error) {
	raw := os.Getenv(name)
	if raw == "" {
		return fallback, nil
	}
	value, err := strconv.Atoi(raw)
	if err != nil || value < minimum || value > maximum {
		return 0, fmt.Errorf("%s must be an integer from %d through %d", name, minimum, maximum)
	}
	return value, nil
}

func int64Env(name string, fallback, minimum, maximum int64) (int64, error) {
	raw := os.Getenv(name)
	if raw == "" {
		return fallback, nil
	}
	value, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || value < minimum || value > maximum {
		return 0, fmt.Errorf("%s must be an integer from %d through %d", name, minimum, maximum)
	}
	return value, nil
}
