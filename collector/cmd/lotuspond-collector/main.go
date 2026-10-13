package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"lotuspond.dev/collector/internal/collector"
)

func main() {
	os.Exit(run())
}

func run() int {
	config, err := collector.ConfigFromEnv()
	if err != nil {
		slog.Error("invalid collector configuration", "error", err)
		return 1
	}

	handler, closeWorkers, err := collector.Open(config)
	if err != nil {
		slog.Error("collector could not start", "error", err)
		return 1
	}
	defer closeWorkers()
	server := &http.Server{
		Addr:              config.ListenAddress,
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       config.RequestTimeout,
		WriteTimeout:      config.RequestTimeout + time.Second,
		IdleTimeout:       30 * time.Second,
		MaxHeaderBytes:    16 << 10,
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	shutdownDone := make(chan struct{})
	go func() {
		defer close(shutdownDone)
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdownCtx); err != nil {
			slog.Error("collector shutdown failed", "error", err)
			// Cancel active requests when the graceful drain budget expires.
			_ = server.Close()
		}
	}()

	slog.Info("collector listening", "address", config.ListenAddress, "spool", config.SpoolDir != "")
	err = server.ListenAndServe()
	// ListenAndServe returns as soon as Shutdown closes listeners, before its
	// in-flight handlers finish. Wait for the drain before closing workers.
	stop()
	<-shutdownDone
	if err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("collector stopped", "error", err)
		return 1
	}
	return 0
}
