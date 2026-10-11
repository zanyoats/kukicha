package app

import "sync"

// Notification is a live, ephemeral message from a job.
type Notification struct {
	JobID   int64  `json:"job_id"`
	Message string `json:"message"`
	Failed  bool   `json:"failed,omitempty"`
}

// JobNotifications broadcasts to connected subscribers without retaining history.
type JobNotifications struct {
	mu          sync.Mutex
	subscribers map[chan Notification]struct{}
}

// Subscribe returns a bounded stream and an idempotent cleanup function.
func (b *JobNotifications) Subscribe() (<-chan Notification, func()) {
	const bufferSize = 16
	subscriber := make(chan Notification, bufferSize)
	b.mu.Lock()
	if b.subscribers == nil {
		b.subscribers = make(map[chan Notification]struct{})
	}
	b.subscribers[subscriber] = struct{}{}
	b.mu.Unlock()
	return subscriber, func() {
		b.mu.Lock()
		defer b.mu.Unlock()
		if _, ok := b.subscribers[subscriber]; ok {
			delete(b.subscribers, subscriber)
			close(subscriber)
		}
	}
}

// Publish drops the new notification for any subscriber whose buffer is full.
func (b *JobNotifications) Publish(notification Notification) {
	b.mu.Lock()
	defer b.mu.Unlock()
	// Hold the lock through sends so unsubscribe cannot close a channel mid-send.
	for subscriber := range b.subscribers {
		select {
		case subscriber <- notification:
		default:
		}
	}
}
