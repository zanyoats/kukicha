package jobs

import (
	"context"
	"log/slog"
	"sync"
	"time"
)

type syncProgress struct {
	logger    *slog.Logger
	message   string
	countKey  string
	mu        sync.Mutex
	processed int
	lastLog   time.Time
	interval  time.Duration
	notify    func(int)
}

func (p *syncProgress) complete(ctx context.Context) {
	p.mu.Lock()
	defer p.mu.Unlock()

	p.processed++

	now := time.Now()
	if p.interval > 0 && now.Sub(p.lastLog) >= p.interval {
		p.logger.InfoContext(ctx, p.message,
			p.countKey, p.processed,
		)
		if p.notify != nil {
			p.notify(p.processed)
		}
		p.lastLog = now
	}
}

func (p *syncProgress) count() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.processed
}
