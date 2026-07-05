# Wrapper: detect claude-code death and resume the session

> REQUIRED SUB-SKILL: superpowers:test-driven-development. Checkbox steps.

**Goal:** When the claude-code process dies, the wrapper must detect it immediately (not wait for the 1800s turn-timeout), relaunch claude with `--continue` to restore the conversation, and resume the in-flight turn by re-submitting its prompt - bounded to 3 consecutive crash-relaunches, after which it fails the turn fast and lets the operator respawn a fresh pod.

**Problem (root cause):** `internal/session/session.go` `watch(proc)` calls `proc.cmd.Wait()`, then on exit sets `state=Dead`, increments the `ClaudeRestarts` metric, and logs - but does NOT fail the in-flight turn and does NOT relaunch claude. So a mid-turn death hangs until `failTimeout` (1800s) fires, and the session stays permanently `Dead` (`Submit` rejects everything). The `ClaudeRestarts` metric name shows restart was intended but never implemented.

**Decisions (user, 2026-06-14):** resume in-place (relaunch `--continue` + re-submit the in-flight turn), fail-fast after a cap of **3** consecutive crash-relaunches. Counter resets on any successful turn.

**Tech:** Go, PTY (creack/pty), interactive claude TUI. Tests run `go test ./internal/session/`.

---

## Task 1: Make the claude process injectable (test seam)

**Files:** Modify `internal/session/pty.go`, `internal/session/session.go`.

- [ ] **Step 1:** In `pty.go` define an interface and have `*claudeProc` satisfy it:

```go
// claudeProcess is the seam the Manager supervises. Real impl is *claudeProc
// over a PTY; tests substitute a fake whose Wait() they control.
type claudeProcess interface {
	Wait() error
	Read(p []byte) (int, error)
	Write(p []byte) (int, error)
	Close() error
}
```

Add the missing methods to `*claudeProc` (Write/Close already exist):

```go
func (c *claudeProc) Wait() error               { return c.cmd.Wait() }
func (c *claudeProc) Read(p []byte) (int, error) { return c.ptmx.Read(p) }
```

- [ ] **Step 2:** Change `spawnClaude` to take a `resume bool` and add `--continue` when resuming. Update `claudeArgs`:

```go
func (c Config) claudeArgs(resume bool) []string {
	args := []string{}
	if c.Model != "" {
		args = append(args, "--model", c.Model)
	}
	if resume {
		// Resume the most recent conversation in the workspace so a relaunched
		// session keeps its context after a crash.
		args = append(args, "--continue")
	}
	return args
}

func spawnClaude(cfg Config, resume bool) (*claudeProc, error) {
	args := cfg.claudeArgs(resume)
	...
}
```

- [ ] **Step 3:** In `session.go` Manager: change `proc claudeProcess`; add fields `spawn func(cfg Config, resume bool) (claudeProcess, error)`, `restarts int`. Add `MaxRestarts int` to `Config`. Change `readPTY(proc claudeProcess)` to use `proc.Read`. In `New()` default `cfg.MaxRestarts<=0 -> 3` and set the spawn fn:

```go
m := &Manager{cfg: cfg, ...}
m.spawn = func(cfg Config, resume bool) (claudeProcess, error) {
	p, err := spawnClaude(cfg, resume)
	if err != nil {
		return nil, err
	}
	return p, nil
}
return m
```

`Start()` uses `mgr.spawn(mgr.cfg, false)` instead of `spawnClaude(mgr.cfg)`.

- [ ] **Step 4:** `go build ./...` clean. Existing tests still pass (`go test ./internal/session/`).

## Task 2: Parametrize clearCurrentLocked with the next state

**Files:** `internal/session/session.go`.

- [ ] `clearCurrentLocked()` -> `clearCurrentLocked(next State)`; set `mgr.state = next`. Update `Complete` (`Ready`) and `failTimeout` (`Ready`). In `Complete`, after clearing, add `mgr.restarts = 0` (a completed turn proves the session healthy).

## Task 3: failTurn helper (fast fail, marks Dead)

**Files:** `internal/session/session.go`.

- [ ] Add:

```go
// failTurn fails the in-flight turn immediately (used when claude died and the
// restart budget is exhausted or a relaunch failed). Marks the session Dead so
// the operator respawns a fresh pod. No-op if id is no longer current.
func (mgr *Manager) failTurn(id, reason string) {
	mgr.mu.Lock()
	if mgr.current != id {
		mgr.mu.Unlock()
		return
	}
	if mgr.timer != nil {
		mgr.timer.Stop()
	}
	now := mgr.now()
	_ = mgr.store.Fail(id, reason, now)
	mgr.clearCurrentLocked(Dead)
	mgr.m.TurnsTotal.WithLabelValues("failed").Inc()
	rec, _ := mgr.store.Get(id)
	mgr.mu.Unlock()
	mgr.log.Warn("turn failed", "turn_id", id, "reason", reason)
	mgr.fireDone(rec)
}
```

## Task 4: watch() recovery + relaunch + resumeTurn

**Files:** `internal/session/session.go`. **Test:** `internal/session/recover_test.go` (new).

- [ ] **Step 1: Write failing tests first** (`recover_test.go`). Use a fake claudeProcess whose `Wait()` blocks on a channel the test closes to simulate death; `Read` blocks until death then returns `io.EOF`; `Write` records bytes. Injected `spawn` returns a fresh fake each call and records `(callCount, lastResumeArg)`. Set `Config{BootTimeout: 30 * time.Millisecond, ...}` so `bootWait` returns via deadline fast (BootTimeout < the 4s minBoot makes the loop exit immediately). Drive `Start()` (or inject the first proc) then `Submit` a turn, then trigger death.

Scenarios:
1. `TestWatch_MidTurnDeath_RelaunchesAndResumes`: submit turn-1, kill claude. Assert: spawn called again with `resume==true`; the new fake's written bytes contain the turn-1 text (re-submitted); `current`=="turn-1"; state==Busy; turn-1 NOT failed.
2. `TestWatch_DeathAtCap_FailsFastAndStaysDead`: set `MaxRestarts:1`; submit; kill (relaunch #1); kill again (over cap). Assert: turn-1 store state==Failed with a "restart budget" reason; OnTurnDone fired; state==Dead; spawn NOT called a 3rd time.
3. `TestWatch_IdleDeath_Relaunches`: no in-flight turn (complete or none); kill claude. Assert spawn called again; state==Ready; no turn submitted.
4. `TestComplete_ResetsRestartCounter`: kill once (restarts=1), relaunch, then a Complete; assert a subsequent death again relaunches (counter was reset so it is not treated as cap+).
5. `TestClaudeArgs_ContinueOnResume`: pure unit - `Config{}.claudeArgs(true)` contains `--continue`, `claudeArgs(false)` does not.
6. `TestWatch_ShutdownDeath_NoRelaunch`: set `stopping=true` (via Shutdown), kill; assert spawn NOT called again, state Dead.

- [ ] **Step 2:** Watch them fail. **Step 3:** Implement:

```go
func (mgr *Manager) watch(proc claudeProcess) {
	err := proc.Wait()
	mgr.mu.Lock()
	if mgr.stopping {
		mgr.state = Dead
		mgr.mu.Unlock()
		mgr.log.Info("claude stopped (shutdown)")
		return
	}
	inFlight := mgr.current
	mgr.restarts++
	attempt := mgr.restarts
	mgr.state = Dead // brief: Submit rejects until relaunch flips to Ready
	mgr.mu.Unlock()

	mgr.m.ClaudeRestarts.Inc()
	mgr.log.Error("claude exited unexpectedly", "err", err, "in_flight_turn", inFlight,
		"attempt", attempt, "max_restarts", mgr.cfg.MaxRestarts, "pty_tail", mgr.ring.tail(800))

	if attempt > mgr.cfg.MaxRestarts {
		mgr.log.Error("claude restart budget exhausted; operator will respawn",
			"max_restarts", mgr.cfg.MaxRestarts)
		if inFlight != "" {
			mgr.failTurn(inFlight, fmt.Sprintf("claude died; restart budget (%d) exhausted", mgr.cfg.MaxRestarts))
		}
		return // state stays Dead
	}

	if rerr := mgr.relaunch(); rerr != nil {
		mgr.mu.Lock()
		mgr.state = Dead
		mgr.mu.Unlock()
		mgr.log.Error("claude relaunch failed; operator will respawn", "err", rerr)
		if inFlight != "" {
			mgr.failTurn(inFlight, fmt.Sprintf("claude relaunch failed: %v", rerr))
		}
		return
	}
	mgr.log.Info("claude relaunched after exit", "attempt", attempt, "resumed_turn", inFlight)
	if inFlight != "" {
		mgr.resumeTurn(inFlight)
	}
}

// relaunch spawns a fresh claude (with --continue when a conversation exists),
// rewires the PTY, restarts the reader+watcher, and waits for boot. The new
// watch goroutine handles the next death (restarts persists across relaunches).
func (mgr *Manager) relaunch() error {
	proc, err := mgr.spawn(mgr.cfg, mgr.shouldResume())
	if err != nil {
		return err
	}
	mgr.mu.Lock()
	mgr.proc, mgr.w = proc, proc
	mgr.state = Booting
	mgr.mu.Unlock()
	go mgr.readPTY(proc)
	go mgr.watch(proc)
	mgr.bootWait() // flips Booting -> Ready
	return nil
}

// shouldResume reports whether a prior conversation exists to --continue. A
// death during the very first boot (no turn ever submitted, none completed)
// relaunches fresh; anything later resumes.
func (mgr *Manager) shouldResume() bool {
	mgr.mu.Lock()
	defer mgr.mu.Unlock()
	return mgr.current != "" || mgr.turnsCompleted > 0 || mgr.transcriptPath != ""
}

// resumeTurn re-submits the in-flight turn's prompt to the restored session,
// keeping the same turn id (so the eventual Stop hook still correlates) and the
// original timeout timer. No-op if the turn was resolved during relaunch.
func (mgr *Manager) resumeTurn(id string) {
	mgr.mu.Lock()
	if mgr.current != id || mgr.state != Ready {
		mgr.mu.Unlock()
		return
	}
	rec, ok := mgr.store.Get(id)
	if !ok || mgr.w == nil {
		mgr.mu.Unlock()
		return
	}
	seq := mgr.cfg.SubmitSeq
	if _, err := mgr.w.Write([]byte(seq.PasteStart + rec.Text + seq.PasteEnd)); err != nil {
		mgr.mu.Unlock()
		mgr.failTurn(id, fmt.Sprintf("resume write paste: %v", err))
		return
	}
	time.Sleep(mgr.cfg.SubmitDelay)
	if _, err := mgr.w.Write([]byte(seq.Submit)); err != nil {
		mgr.mu.Unlock()
		mgr.failTurn(id, fmt.Sprintf("resume write submit: %v", err))
		return
	}
	mgr.state = Busy
	mgr.mu.Unlock()
	mgr.log.Info("resumed in-flight turn after relaunch", "turn_id", id)
}
```

Replace the existing `watch` body. Keep the `ClaudeRestarts` metric (now accurate). `turn.Record.Text` holds the original prompt (json:"-", set by store.Create in Submit).

- [ ] **Step 4:** Tests green. **Step 5:** gofmt + `go vet ./...`. **Step 6:** Full `go test ./...` green (the existing `TestStart_RealPTYWithCat` still passes; it uses a real proc via the default spawn).

## Verification
- [ ] `go test ./...` fully green; gofmt + vet clean.
- [ ] requesting-code-review (focus: lock discipline across the relaunch goroutine chain, no deadlock holding mgr.mu during bootWait, no double-fire of OnTurnDone, restarts counter races).
- [ ] MEMORY.md: record the watch()-never-restarted gap + the resume-with-cap-3 design.
- [ ] Merge to wrapper main; deploy bundles with the skip-push fix (already on main) in one wrapper image bump.
