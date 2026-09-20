# ⚡ Quick Start Guide - CorvinOS Premium Video Generator

**Create a professional 51-second video in minutes!**

---

## 🎯 Ultra-Fast Setup (2 minutes)

```bash
# 1. Navigate to the generator directory
cd /home/shumway/projects/CorvinOS/video_generator

# 2. Run setup (one-time)
bash setup.sh

# 3. Generate your video
python3 corvin_video_generator.py
```

**Done!** Your video will be at:
```
/home/shumway/projects/CorvinOS/outputs/corvinos_premium_final.mp4
```

---

## 📋 System Requirements

✅ **Already Available:**
- Linux OS
- Python 3.11+
- FFmpeg (for video encoding)
- Narration file: `/tmp/narration_espeak_natural.aac` (704 KB)

⚠️ **May Need to Install:**
```bash
# If pip is missing:
sudo apt-get install python3-pip

# Optional (for better quality):
sudo apt-get install blender        # Native 3D rendering
sudo apt-get install python3-manim  # Mathematical animations
```

---

## 🎬 One-Command Generation

```bash
cd /home/shumway/projects/CorvinOS/video_generator
python3 corvin_video_generator.py
```

That's it! The pipeline will:

| Phase | Task | Time |
|-------|------|------|
| 1 | Generate Blender 3D logo | 1-3 min |
| 2 | Create architecture diagrams | 1-2 min |
| 3 | Produce data flow visuals | 3-5 min |
| 4 | Add particle effects | 1-2 min |
| 5 | Assemble & add audio | 5-10 min |
| **Total** | **Complete video** | **12-25 min** |

---

## ✅ What You'll Get

- ✓ 1920×1080 Full HD resolution
- ✓ 51 seconds @ 25 fps (PAL)
- ✓ Professional H.264 encoding
- ✓ 50 Mbps bitrate (broadcast quality)
- ✓ Synchronized narration audio
- ✓ Animated color grading
- ✓ Particle effect overlays
- ✓ Ready for YouTube/streaming

---

## 🔧 Common Commands

### Check if dependencies are installed
```bash
python3 corvin_video_generator.py --check-deps
```

### Generate only one phase (for debugging)
```bash
# Phase 1 (Blender intro)
python3 corvin_video_generator.py --phase 1

# Phase 3 (Data flows)
python3 corvin_video_generator.py --phase 3
```

### Use custom configuration
```bash
python3 corvin_video_generator.py --config my_custom_settings.yaml
```

### View detailed logs
```bash
tail -f logs/video_generation.log
```

---

## 📁 Project Structure

```
/home/shumway/projects/CorvinOS/video_generator/
├── corvin_video_generator.py    ← Main script (run this!)
├── config/video_settings.yaml   ← Customize here
├── phases/                       ← Each phase module
├── output/                       ← Intermediate files
├── logs/                         ← Execution logs
├── setup.sh                      ← Setup script
└── README.md                     ← Full documentation
```

---

## 🎨 Customization (Optional)

### Change output location
Edit `config/video_settings.yaml`:
```yaml
output:
  file: "/path/to/my_video.mp4"
```

### Adjust video quality
```yaml
video:
  crf: 18        # 0-51; lower = better (slower)
  preset: "slow" # fast, medium, slow, slower
```

### Change colors
```yaml
color_grading:
  primary_color: "#FFD700"      # Gold
  secondary_color: "#FF006E"    # Magenta
```

See `README.md` for all options.

---

## 🚨 Troubleshooting

### "ffmpeg not found"
```bash
sudo apt-get install ffmpeg
```

### "Module not found" errors
```bash
pip install pyyaml numpy pillow
# Or: pip3 install pyyaml numpy pillow
```

### Video file too large
This is normal! Final MP4 is typically 30-40 MB for 51 seconds @ 50 Mbps.

### Audio out of sync
Your narration file should be exactly 51 seconds. Current file is detected automatically.

---

## 📊 Expected Output

```
Final Output: /home/shumway/projects/CorvinOS/outputs/corvinos_premium_final.mp4
├─ Size: 30-40 MB
├─ Duration: 51 seconds
├─ Resolution: 1920×1080
├─ Frame Rate: 25 fps
├─ Bitrate: 50 Mbps
└─ Quality: Broadcast-Ready ✓
```

Verify it worked:
```bash
ffprobe corvinos_premium_final.mp4
# Or watch it:
ffplay corvinos_premium_final.mp4
```

---

## 🎯 Next Steps

1. **First run:** `python3 corvin_video_generator.py`
2. **Wait:** 12-25 minutes for complete rendering
3. **Check output:** View `/home/shumway/projects/CorvinOS/outputs/corvinos_premium_final.mp4`
4. **Share:** Use on YouTube, website, presentations!

---

## 📞 Need Help?

- **Full docs:** See `README.md`
- **Configuration:** See `config/video_settings.yaml` (well-commented)
- **Logs:** Check `logs/video_generation.log`
- **Phases:** See `phases/` for individual phase details

---

## ⚡ TL;DR

```bash
cd /home/shumway/projects/CorvinOS/video_generator
bash setup.sh
python3 corvin_video_generator.py
# Wait 12-25 minutes
# Enjoy your video at: ~/CorvinOS/outputs/corvinos_premium_final.mp4
```

**That's all you need!** 🚀
