package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"

	gatewayserver "ai-agent-platform/gateway/internal/gateway"
)

func main() {
	config, err := gatewayserver.LoadConfig()
	if err != nil {
		slog.Error("invalid gateway configuration", "error", err)
		os.Exit(1)
	}
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: logLevel(config.LogLevel)}))
	handler := gatewayserver.NewHandler(config, logger)
	server := gatewayserver.NewServer(config, handler)

	stopContext, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	serverErrors := make(chan error, 1)
	go func() {
		logger.Info("gateway listening", "address", config.ListenAddress, "upstream", config.UpstreamURL.Redacted())
		serverErrors <- server.ListenAndServe()
	}()

	select {
	case err := <-serverErrors:
		if !errors.Is(err, http.ErrServerClosed) {
			logger.Error("gateway stopped unexpectedly", "error", err)
			os.Exit(1)
		}
		return
	case <-stopContext.Done():
		logger.Info("gateway shutdown started")
	}

	shutdownContext, cancel := context.WithTimeout(context.Background(), config.ShutdownTimeout)
	defer cancel()
	if err := gatewayserver.Shutdown(shutdownContext, server); err != nil {
		logger.Error("gateway graceful shutdown failed", "error", err)
		os.Exit(1)
	}
	logger.Info("gateway shutdown completed")
}

func logLevel(level string) slog.Level {
	switch level {
	case "debug":
		return slog.LevelDebug
	case "warn":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}
