package launch

import (
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"testing"
)

func TestCorvinOSString(t *testing.T) {
	a := &CorvinOS{}
	if a.String() != "CorvinOS" {
		t.Fatalf("expected CorvinOS, got %q", a.String())
	}
}

func TestCorvinOSBinaryNotFound(t *testing.T) {
	orig := corvinLookPath
	t.Cleanup(func() { corvinLookPath = orig })
	corvinLookPath = func(string) (string, error) { return "", exec.ErrNotFound }

	a := &CorvinOS{}
	_, err := a.binary()
	if err == nil {
		t.Fatal("expected error when corvin binary is missing")
	}
	if !errors.Is(err, nil) && err.Error() == "" {
		t.Fatal("expected a helpful error message")
	}
}

func TestCorvinOSBinaryFound(t *testing.T) {
	orig := corvinLookPath
	t.Cleanup(func() { corvinLookPath = orig })
	corvinLookPath = func(name string) (string, error) {
		if name == "corvin" {
			return "/usr/local/bin/corvin", nil
		}
		return "", exec.ErrNotFound
	}

	a := &CorvinOS{}
	bin, err := a.binary()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if bin != "/usr/local/bin/corvin" {
		t.Fatalf("expected /usr/local/bin/corvin, got %q", bin)
	}
}

func TestCorvinOSPaths(t *testing.T) {
	orig := corvinUserHome
	t.Cleanup(func() { corvinUserHome = orig })
	corvinUserHome = func() (string, error) { return "/home/testuser", nil }

	a := &CorvinOS{}
	paths := a.Paths()
	if len(paths) == 0 {
		t.Fatal("expected at least one config path")
	}
	want := "/home/testuser/.config/corvin-launcher/config.json"
	if paths[0] != want {
		t.Fatalf("expected %q, got %q", want, paths[0])
	}
}

func TestCorvinOSInstalled(t *testing.T) {
	orig := corvinLookPath
	t.Cleanup(func() { corvinLookPath = orig })

	corvinLookPath = func(string) (string, error) { return "/usr/bin/corvin", nil }
	a := &CorvinOS{}
	if !a.installed() {
		t.Fatal("expected installed() to return true when binary found")
	}

	corvinLookPath = func(string) (string, error) { return "", exec.ErrNotFound }
	if a.installed() {
		t.Fatal("expected installed() to return false when binary not found")
	}
}

func TestCorvinOSRunForwardsArgs(t *testing.T) {
	if os.Getenv("CORVIN_AGENTS_SKIP_LIVE") == "1" ||
		os.Getenv("CORVIN_AGENTS_SKIP_LIVE") == "1" {
		t.Skip("live tests skipped")
	}

	orig := corvinLookPath
	origCmd := corvinCommand
	origRead := corvinReadFile
	origHome := corvinUserHome
	t.Cleanup(func() {
		corvinLookPath = orig
		corvinCommand = origCmd
		corvinReadFile = origRead
		corvinUserHome = origHome
	})

	// Disable auto-update to prevent pip side-effects in this test.
	f := false
	cfgBytes, _ := json.Marshal(launcherConfig{AutoUpdate: &f})
	corvinReadFile = func(string) ([]byte, error) { return cfgBytes, nil }
	corvinUserHome = func() (string, error) { return "/tmp/testuser", nil }

	var capturedArgs []string
	corvinLookPath = func(name string) (string, error) {
		if name == "corvin" {
			return "/usr/bin/corvin", nil
		}
		return "", exec.ErrNotFound
	}
	corvinCommand = func(name string, args ...string) *exec.Cmd {
		capturedArgs = append([]string{name}, args...)
		// Return a cmd that does nothing
		return exec.Command("true")
	}

	a := &CorvinOS{}
	_ = a.Run("", nil, []string{})

	if len(capturedArgs) < 2 {
		t.Fatalf("expected gateway start args, got %v", capturedArgs)
	}
	if capturedArgs[1] != "gateway" || capturedArgs[2] != "start" {
		t.Fatalf("expected 'gateway start', got %v", capturedArgs[1:])
	}
}

func TestCorvinOSAutoUpdateDefaultOn(t *testing.T) {
	origRead := corvinReadFile
	origHome := corvinUserHome
	t.Cleanup(func() {
		corvinReadFile = origRead
		corvinUserHome = origHome
	})
	corvinReadFile = func(string) ([]byte, error) { return nil, os.ErrNotExist }
	corvinUserHome = func() (string, error) { return "/tmp/testuser", nil }

	a := &CorvinOS{}
	if !a.autoUpdateEnabled() {
		t.Fatal("expected auto_update to default to true when config is absent")
	}
}

func TestCorvinOSAutoUpdateExplicitFalse(t *testing.T) {
	origRead := corvinReadFile
	origHome := corvinUserHome
	t.Cleanup(func() {
		corvinReadFile = origRead
		corvinUserHome = origHome
	})
	f := false
	cfgBytes, _ := json.Marshal(launcherConfig{AutoUpdate: &f})
	corvinReadFile = func(string) ([]byte, error) { return cfgBytes, nil }
	corvinUserHome = func() (string, error) { return "/tmp/testuser", nil }

	a := &CorvinOS{}
	if a.autoUpdateEnabled() {
		t.Fatal("expected auto_update to be false when explicitly set")
	}
}

func TestCorvinOSMaybeAutoUpdateSkippedWhenDisabled(t *testing.T) {
	origRead := corvinReadFile
	origHome := corvinUserHome
	origLook := corvinLookPath
	origCmd := corvinCommand
	t.Cleanup(func() {
		corvinReadFile = origRead
		corvinUserHome = origHome
		corvinLookPath = origLook
		corvinCommand = origCmd
	})
	f := false
	cfgBytes, _ := json.Marshal(launcherConfig{AutoUpdate: &f})
	corvinReadFile = func(string) ([]byte, error) { return cfgBytes, nil }
	corvinUserHome = func() (string, error) { return "/tmp/testuser", nil }

	pipCalled := false
	corvinLookPath = func(name string) (string, error) {
		if name == "pip" || name == "pip3" {
			return "/usr/bin/pip", nil
		}
		return "", exec.ErrNotFound
	}
	corvinCommand = func(name string, args ...string) *exec.Cmd {
		if name == "/usr/bin/pip" {
			pipCalled = true
		}
		return exec.Command("true")
	}

	a := &CorvinOS{}
	a.maybeAutoUpdate()

	if pipCalled {
		t.Fatal("pip must not be called when auto_update is false")
	}
}

func TestCorvinOSMaybeAutoUpdateRunsPip(t *testing.T) {
	origRead := corvinReadFile
	origHome := corvinUserHome
	origLook := corvinLookPath
	origCmd := corvinCommand
	t.Cleanup(func() {
		corvinReadFile = origRead
		corvinUserHome = origHome
		corvinLookPath = origLook
		corvinCommand = origCmd
	})
	tr := true
	cfgBytes, _ := json.Marshal(launcherConfig{AutoUpdate: &tr})
	corvinReadFile = func(string) ([]byte, error) { return cfgBytes, nil }
	corvinUserHome = func() (string, error) { return "/tmp/testuser", nil }

	var pipArgs []string
	corvinLookPath = func(name string) (string, error) {
		if name == "pip" {
			return "/usr/bin/pip", nil
		}
		return "", exec.ErrNotFound
	}
	corvinCommand = func(name string, args ...string) *exec.Cmd {
		if name == "/usr/bin/pip" {
			pipArgs = args
		}
		return exec.Command("true")
	}

	a := &CorvinOS{}
	a.maybeAutoUpdate()

	if len(pipArgs) == 0 {
		t.Fatal("expected pip to be called when auto_update is true")
	}
	if pipArgs[0] != "install" {
		t.Fatalf("expected 'install', got %q", pipArgs[0])
	}
	found := false
	for _, arg := range pipArgs {
		if arg == corvinPipPackage {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected %q in pip args, got %v", corvinPipPackage, pipArgs)
	}
}
