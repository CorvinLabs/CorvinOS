# CorvinOS Video Generator v2 — Quick Start Guide

## ⚡ 5-Minute Setup

### Step 1: Prerequisites Check (1 min)

```bash
# Check FFmpeg
ffmpeg -version
ffprobe -version

# If missing:
sudo apt-get install ffmpeg
```

### Step 2: Set API Key (1 min)

```bash
# Set OpenAI API key
export OPENAI_API_KEY="sk-proj-YOUR_KEY_HERE"

# Verify it's set
echo $OPENAI_API_KEY
```

### Step 3: Run Pipeline (2-3 min)

```bash
# Navigate to generator
cd /home/shumway/projects/CorvinOS/video_generator_v2

# Run full pipeline
python3 pipeline_orchestrator.py
```

### Step 4: Check Output (1 min)

```bash
# Video should be at:
ls -lh /tmp/corvinos_video_v2/corvinos_final.mp4

# Verify with ffprobe:
ffprobe /tmp/corvinos_video_v2/corvinos_final.mp4
```

---

## 📋 Expected Output

```
✓ PowerPoint Slides Generated
✓ SVG Diagrams Generated (4 diagrams)
✓ OpenAI TTS Narration Generated
✓ Final Video Composed

Output: /tmp/corvinos_video_v2/corvinos_final.mp4
Duration: ~130 seconds
Size: ~100-150 MB
Codec: H.264
Audio: AAC 192k stereo
```

---

## 🧪 Run Tests

### E2E Tests (5 min)

```bash
cd tests
python3 -m pytest test_e2e_full_pipeline.py -v
```

**Expected:** ✅ 8/8 PASSED

### Adversarial Tests (2 min)

```bash
python3 -m pytest test_adversarial_review.py -v
```

**Expected:** ✅ 8/8 PASSED

---

## 📝 Configuration

Default config at `config/pipeline_config.yaml` includes:

- 5 PowerPoint slides (25 seconds total)
- 4 SVG diagrams (32 seconds total)
- 6 German narration segments (33 seconds total)

Total video: **~130 seconds** of content

### Customize Narration

Edit `config/pipeline_config.yaml`:

```yaml
segments:
  - name: intro
    text: "Your custom German text here"
    duration_seconds: 5
```

---

## 🎯 Phase Breakdown

### Phase 1: PowerPoint (30s)
- Auto-generates 5 default slides
- Or: Converts your own `.pptx` file
- Output: `phase1_pptx/corvinos_slides.mp4`

### Phase 2: SVG Diagrams (2-3 min)
- Four Pillars animation
- Architecture stack diagram
- Data flow visualization
- Timeline/roadmap

### Phase 3: OpenAI Voice (10-20s)
- Converts German text to natural speech
- Uses `nova` voice model
- Validates audio quality
- Output: `phase3_voice/narration_final.aac`

### Phase 4: Composition (5-10 min)
- Concatenates all video segments
- Mixes in narration audio
- Applies color grading
- Generates final MP4

---

## ✅ Validation

Pipeline auto-validates:

✅ PowerPoint video has content (no black frames)  
✅ All 4 SVG diagrams generated  
✅ Audio is audible (volume > -30dB)  
✅ Final video has both video and audio streams  
✅ Duration matches expected (~130s)  

---

## 🐛 Common Issues

### "OPENAI_API_KEY not set"
```bash
export OPENAI_API_KEY="sk-..."
python3 pipeline_orchestrator.py
```

### "ffmpeg: command not found"
```bash
sudo apt-get install ffmpeg
```

### "Output video has no audio"
Check that `phase3_voice/narration_final.aac` exists:
```bash
ls -la /tmp/corvinos_video_v2/phase3_voice/
```

### "Video quality is poor"
Increase CRF (lower = better):
```yaml
video:
  crf: 16  # Default: 18
```

---

## 📊 File Structure

```
video_generator_v2/
├── pipeline_orchestrator.py    ← Main entry point
├── audio_generator/            
│   └── openai_tts.py          ← TTS Engine
├── asset_generators/
│   ├── powerpoint_generator.py ← PPTX to Video
│   └── svg_generator.py        ← Diagrams
├── tests/
│   ├── test_e2e_full_pipeline.py        ← 8 E2E tests
│   └── test_adversarial_review.py       ← 8 Robustness tests
├── config/
│   └── pipeline_config.yaml    ← Configuration
├── outputs/                    ← Generated videos
└── README.md                   ← Full documentation
```

---

## 🎬 Examples

### Generate Default Video
```bash
python3 pipeline_orchestrator.py
```

### Use Custom PPTX
```python
from pipeline_orchestrator import RenderPipeline

pipeline = RenderPipeline()
# Modify phase 1 to use custom file:
# pptx_video = pipeline.phase1_powerpoint(pptx_file="my_presentation.pptx")
```

### Generate Just PowerPoint
```python
from asset_generators.powerpoint_generator import PowerPointGenerator

gen = PowerPointGenerator()
video = gen.execute()
# Output: /tmp/corvinos_pptx/corvinos_slides.mp4
```

### Generate Just Diagrams
```python
from asset_generators.svg_generator import SVGDiagramGenerator

gen = SVGDiagramGenerator()
diagrams = gen.execute()
# Returns: {"four_pillars": "...", "architecture": "...", ...}
```

---

## 🚀 Next Steps

1. ✅ Run pipeline and generate video
2. ✅ Run E2E test suite
3. ✅ Run adversarial test suite
4. ✅ Verify video quality
5. → Phase 3: Code review and findings iteration

---

## 📖 Full Documentation

See `README.md` for complete documentation including:
- Architecture overview
- API reference
- Configuration options
- Performance metrics
- Troubleshooting guide

---

## ⏱️ Timeline

| Step | Time |
|------|------|
| Prerequisites | 1 min |
| API Key | 1 min |
| Run Pipeline | 2-3 min |
| Verify Output | 1 min |
| **Total** | **5-6 min** |

---

## 🎯 Success Criteria

✅ Video file created: `/tmp/corvinos_video_v2/corvinos_final.mp4`  
✅ Duration: ~130 seconds  
✅ Both video and audio streams present  
✅ No errors in logs  
✅ File size: 100-150 MB  

---

**Status:** 🟢 Ready to use  
**Phase:** 2 (Build Complete)  
**Next:** Phase 3 (Testing & Iteration)

Happy video generation! 🎬
