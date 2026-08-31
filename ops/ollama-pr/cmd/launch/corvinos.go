package launch

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/ollama/ollama/envconfig"
)

// CorvinOS is intentionally not an Editor integration: launch owns one
// primary model and the local Ollama endpoint, while CorvinOS keeps its own
// bridge/channel selection UX after startup.
type CorvinOS struct{}

func (a *CorvinOS) String() string { return "CorvinOS" }

var (
	corvinLookPath  = exec.LookPath
	corvinCommand   = exec.Command
	corvinUserHome  = os.UserHomeDir
	corvinOllamaURL = envconfig.ConnectableHost
	corvinReadFile  = os.ReadFile
)

const (
	corvinPipPackage  = "corvinos"
	corvinInstallHint = "pip install corvinos"
)

// launcherConfig is the on-disk shape of ~/.config/corvin-launcher/config.json.
// Unknown keys are ignored so future versions can add fields freely.
type launcherConfig struct {
	AutoUpdate *bool `json:"auto_update"`
}

func (a *CorvinOS) configPath() (string, error) {
	home, err := corvinUserHome()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".config", "corvin-launcher", "config.json"), nil
}

func (a *CorvinOS) readConfig() launcherConfig {
	p, err := a.configPath()
	if err != nil {
		return launcherConfig{}
	}
	data, err := corvinReadFile(p)
	if err != nil {
		return launcherConfig{} // absent file → all defaults
	}
	var cfg launcherConfig
	_ = json.Unmarshal(data, &cfg)
	return cfg
}

// autoUpdateEnabled returns true unless the config file explicitly sets
// auto_update to false. Default-on: absent key or absent file both mean enabled.
func (a *CorvinOS) autoUpdateEnabled() bool {
	cfg := a.readConfig()
	if cfg.AutoUpdate == nil {
		return true
	}
	return *cfg.AutoUpdate
}

// maybeAutoUpdate runs "pip install --upgrade --quiet corvinos" when auto-update
// is enabled. Errors are non-fatal; ensureInstalled handles first-install separately.
func (a *CorvinOS) maybeAutoUpdate() {
	if !a.autoUpdateEnabled() {
		return
	}
	pip, err := corvinLookPath("pip")
	if err != nil {
		pip, err = corvinLookPath("pip3")
	}
	if err != nil {
		return // pip unavailable — ensureInstalled will surface the error
	}
	fmt.Fprintln(os.Stderr, "Checking for CorvinOS updates …")
	cmd := corvinCommand(pip, "install", "--upgrade", "--quiet", corvinPipPackage)
	cmd.Stdout = os.Stderr
	cmd.Stderr = os.Stderr
	_ = cmd.Run()
}

func (a *CorvinOS) binary() (string, error) {
	bin, err := corvinLookPath("corvin")
	if errors.Is(err, exec.ErrNotFound) {
		return "", fmt.Errorf(
			"CorvinOS is not installed. Install it with:\n\n  pip install corvinos\n\nThen re-run:\n  ollama launch corvinos",
		)
	}
	return bin, err
}

func (a *CorvinOS) Paths() []string {
	home, _ := corvinUserHome()
	return []string{
		filepath.Join(home, ".config", "corvin-launcher", "config.json"),
	}
}

func (a *CorvinOS) Configure(model string) error {
	bin, err := a.binary()
	if err != nil {
		return err
	}
	ollamaURL := corvinOllamaURL()
	return corvinCommand(bin,
		"config", "set", "ollama-url", ollamaURL,
	).Run()
}

func (a *CorvinOS) installed() bool {
	_, err := corvinLookPath("corvin")
	return err == nil
}

func (a *CorvinOS) ensureInstalled() error {
	if a.installed() {
		return nil
	}

	// Check pip is available
	pip, err := corvinLookPath("pip")
	if err != nil {
		pip, err = corvinLookPath("pip3")
	}
	if err != nil {
		return fmt.Errorf(
			"CorvinOS is not installed and pip is not available.\n\n" +
				"Install pip first:\n  https://pip.pypa.io/en/stable/installation/\n\n" +
				"Then install CorvinOS:\n  pip install corvinos",
		)
	}

	fmt.Fprintln(os.Stderr, "Installing CorvinOS …")
	cmd := corvinCommand(pip, "install", corvinPipPackage)
	cmd.Stdout = os.Stderr
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

func (a *CorvinOS) Run(model string, _ []LaunchModel, args []string) error {
	a.maybeAutoUpdate()
	if err := a.ensureInstalled(); err != nil {
		return err
	}

	bin, err := a.binary()
	if err != nil {
		return err
	}

	// If model is given, update config before starting
	if model != "" && !strings.Contains(model, "cloud") {
		ollamaURL := corvinOllamaURL()
		_ = corvinCommand(bin, "config", "set", "ollama-url", ollamaURL).Run()
		_ = corvinCommand(bin, "config", "set", "model", model).Run()
	}

	runArgs := append([]string{"gateway", "start"}, args...)
	return corvinCommand(bin, runArgs...).Run()
}
