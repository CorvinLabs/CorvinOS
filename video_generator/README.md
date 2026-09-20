# CorvinOS Premium Video Generator

**Professional Broadcast-Quality Video Production Pipeline**

Generate a stunning 51-second premium marketing video for CorvinOS with professional animations, particle effects, and color grading.

---

## 📺 Output Specifications

| Aspect | Value |
|--------|-------|
| **Duration** | 51 seconds |
| **Resolution** | 1920×1080 (Full HD) |
| **Frame Rate** | 25 fps (PAL) |
| **Codec** | H.264 (libx264) |
| **Bitrate** | 50 Mbps |
| **Color Space** | YUV420p (Broadcast Standard) |
| **Audio** | AAC 192 kbps, 24 kHz |
| **File Format** | MP4 |
| **File Size** | ~30-40 MB |
| **Quality** | Broadcast-Ready ✓ |

---

## 🚀 Quick Start

### 1. One-Command Setup

```bash
cd /home/shumway/projects/CorvinOS/video_generator
bash setup.sh
```

This will:
- Verify Python 3.8+ is installed
- Check for required tools (ffmpeg, etc.)
- Identify optional tools (Blender, Manim, etc.) for enhanced quality
- Create Python virtual environment
- Install dependencies
- Create output directories

### 2. Generate Your Video

```bash
python3 corvin_video_generator.py
```

That's it! The complete 5-phase pipeline will:
1. Generate 3D logo animation
2. Create architecture diagrams
3. Produce data flow visualizations
4. Add particle effects
5. Compose final video with audio and color grading

**Output:** `/home/shumway/projects/CorvinOS/outputs/corvinos_premium_final.mp4`

---

## 📋 Pipeline Overview

### Phase 1: Blender 3D Logo (5 seconds)
- Animated CorvinOS ring logo
- Professional 3D rendering
- **Fallback:** High-quality CGI using FFmpeg gradients
- Duration: 5 seconds

### Phase 2: Manim Diagrams (12 seconds)
- Four Pillars of CorvinOS (6s)
  - Voice, Encryption, Deduplication, A2A Bridge
- Architecture Stack (6s)
  - API Gateway → Orchestration → Worker → Encryption → Audit
- **Fallback:** Static images with FFmpeg text overlays
- Duration: 12 seconds

### Phase 3: SVG Data Flows (24 seconds)
- Healthcare Data Flow (8s) - HIPAA compliance
- Finance Transaction Flow (8s) - Security & deduplication
- Government Sovereignty Flow (8s) - Data protection
- **Fallback:** FFmpeg text-based animations
- Duration: 24 seconds

### Phase 4: Particle Effects (51 seconds)
- Floating particle overlay
- Matches entire video duration
- Applied with lighten blend mode
- Duration: 51 seconds (overlay)

### Phase 5: Final Composition (Assembly)
- Concatenates all video segments
- Blends particle effects
- Adds narration audio
- Applies color grading
- Outputs broadcast-quality file

---

## 🛠️ Usage Examples

### Check Dependencies
```bash
python3 corvin_video_generator.py --check-deps
```

### Generate Specific Phase
```bash
# Phase 1 only (Blender)
python3 corvin_video_generator.py --phase 1

# Phase 3 only (SVG Flows)
python3 corvin_video_generator.py --phase 3
```

### Custom Configuration
```bash
python3 corvin_video_generator.py --config my_settings.yaml
```

### Verbose Output
```bash
python3 corvin_video_generator.py --verbose
```

---

## 📁 Directory Structure

```
video_generator/
├── config/
│   └── video_settings.yaml          # Main configuration file
├── phases/
│   ├── __init__.py
│   ├── base_utils.py                # Shared utilities
│   ├── phase1_blender_intro.py      # 3D logo animation
│   ├── phase2_manim_diagrams.py     # Architecture diagrams
│   ├── phase3_svg_flows.py          # Data flow graphics
│   ├── phase4_particle_effects.py   # Particle overlay
│   └── phase5_compositor.py         # Final assembly
├── output/                          # Generated videos & frames
│   ├── assets/
│   ├── frames/
│   └── phase*.mp4                   # Intermediate outputs
├── logs/
│   └── video_generation.log         # Execution logs
├── corvin_video_generator.py        # Main orchestrator
├── setup.sh                         # Setup script
├── requirements.txt                 # Python dependencies
└── README.md                        # This file
```

---

## ⚙️ Configuration

Edit `config/video_settings.yaml` to customize:

```yaml
# Output location
output:
  file: "/home/shumway/projects/CorvinOS/outputs/corvinos_premium_final.mp4"

# Video quality
video:
  fps: 25
  crf: 18  # 0-51; lower = better quality
  bitrate: "50M"

# Audio
audio:
  narration_file: "/tmp/narration_espeak_natural.aac"
  bitrate: "192k"

# Color grading theme
color_grading:
  primary_color: "#FFD700"      # Gold
  secondary_color: "#FF006E"    # Magenta
  tertiary_color: "#00D9FF"     # Cyan
  accent_color: "#00F077"       # Green
```

---

## 📊 Quality Tuning

### For FASTER renders (trade quality):
```yaml
video:
  preset: "fast"      # fast, medium, slow, slower
  crf: 23            # Higher = lower quality, faster
```

### For MAXIMUM quality (slower rendering):
```yaml
video:
  preset: "slower"    # Best compression
  crf: 16            # Lower = higher quality, slower
  bitrate: "80M"     # Increase bitrate
```

---

## 🔧 Troubleshooting

### FFmpeg not found
```bash
sudo apt-get install ffmpeg
```

### Out of disk space
Check temporary directory:
```bash
df -h /tmp/
# If full, adjust in config:
output:
  temp_dir: "/path/to/bigger/disk"
```

### Audio sync issues
Ensure narration file exists and is valid:
```bash
ffprobe /tmp/narration_espeak_natural.aac
```

### Memory issues
Reduce parallel processing in config:
```yaml
performance:
  parallel_phases: false
  max_threads: 2
```

---

## 📈 Expected Rendering Times

| Phase | Duration | Notes |
|-------|----------|-------|
| Phase 1 (Blender) | 2-3 min | Native: 5-10 min; Fallback: 1-2 min |
| Phase 2 (Manim) | 1-2 min | Fallback: <1 min |
| Phase 3 (SVG) | 3-5 min | 3 flows × 8s each |
| Phase 4 (Particles) | 1-2 min | 51 seconds @ 25fps |
| Phase 5 (Compositor) | 5-10 min | Concat + blend + audio + color grade |
| **Total** | **12-25 min** | Depends on CPU, GPU, disk speed |

---

## 🎨 Color Palette (CorvinOS Theme)

```
Primary Gold:        #FFD700
Secondary Magenta:   #FF006E
Tertiary Cyan:       #00D9FF
Accent Green:        #00F077
Dark Background:     #0f1320 (Navy)
```

---

## 🔗 Integration Points

### Voice Narration
- **File:** `/tmp/narration_espeak_natural.aac`
- **Format:** AAC, 24 kHz recommended
- **Duration:** Should match 51 seconds (will be cut to video length)
- **Bitrate:** 192 kbps (adjustable in config)

### Existing Assets
The pipeline automatically detects and uses:
- Video files from phases 1-4
- Audio file from config
- Color profile settings
- Font files (system defaults)

---

## 📝 Logs & Debugging

View execution log:
```bash
tail -f logs/video_generation.log
```

All operations are logged with timestamps and detailed error messages.

---

## ✅ Verification

After generation, verify the output:

```bash
# Check file exists and size
ls -lh /home/shumway/projects/CorvinOS/outputs/corvinos_premium_final.mp4

# Check video properties
ffprobe -v error -select_streams v:0 \
  -show_entries stream=width,height,r_frame_rate,duration \
  -of default=noprint_wrappers=1 \
  corvinos_premium_final.mp4

# Test playback (if X server available)
ffplay -window_title "CorvinOS Premium" corvinos_premium_final.mp4
```

Expected output:
- **Resolution:** 1920×1080
- **Frame rate:** 25 fps
- **Duration:** ~51 seconds
- **Audio:** AAC 192 kbps
- **File size:** 30-40 MB

---

## 🚀 Advanced Usage

### Parallel Processing (Experimental)
```yaml
performance:
  parallel_phases: true
  max_threads: 4
```
⚠️ Use with caution - requires significant resources

### GPU Acceleration (NVIDIA/CUDA)
```yaml
performance:
  gpu_acceleration: true
```
Requires NVIDIA GPU + CUDA toolkit

### Custom Color LUT
Place a `.cube` LUT file at:
```
config/corvinos_color_profile.cube
```

---

## 📦 Dependencies

### Required
- Python 3.8+
- FFmpeg 4.0+
- FFprobe (included with FFmpeg)

### Optional (with fallbacks)
- Blender 3.0+ (fallback: FFmpeg gradients)
- Manim (fallback: static images)
- ImageMagick (fallback: FFmpeg filters)

### Python Packages
- PyYAML (config parsing)
- NumPy (optional, for advanced effects)
- Pillow (image processing)

---

## 🎯 Performance Tips

1. **Use SSD storage** - Faster disk I/O significantly speeds up rendering
2. **Close other applications** - Frees up CPU and memory
3. **Run during off-hours** - Video rendering is CPU-intensive
4. **Monitor resources** - Use `htop` to watch CPU/memory usage
5. **Enable GPU** - If available, can speed up encoding 5-10x

---

## 🔐 Quality Assurance

The pipeline includes:
- ✓ Dependency verification
- ✓ Output validation
- ✓ File integrity checks
- ✓ Detailed logging
- ✓ Error recovery with fallbacks

---

## 📞 Support

### Issue: Render too slow
- Solution: Use `preset: "fast"` or increase `crf` value

### Issue: Video quality poor
- Solution: Use `preset: "slower"` and lower `crf` value

### Issue: Out of memory
- Solution: Close other apps, reduce `max_threads`

### Issue: Audio out of sync
- Solution: Check narration file duration matches 51 seconds

---

## 📄 License

Part of the CorvinOS project.

---

## 🎬 Final Notes

- The pipeline creates **production-ready** broadcast-quality video
- All intermediate files are preserved for debugging (set `keep_intermediates: false` to clean up)
- Total file size includes all phases; final output is typically 30-40 MB
- The pipeline is fully automated - just run the command and wait!

**Happy video generation! 🚀**
