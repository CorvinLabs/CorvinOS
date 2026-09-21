# Video Producer Renderers — Performance Benchmarking

## Renderer Performance Profile (Estimated)

| Renderer | Time/Frame | Memory | Notes |
|---|---|---|---|
| **SVG** | 100-500ms | 50MB | Fast; depends on shape complexity |
| **Effects** | 50-200ms | 200MB | Per-frame; transitions slower |
| **Screencast** | 100-1000ms | 500MB | FFmpeg overhead; audio sync |
| **Blender** | 5-30s | 2-4GB | Async; GPU can 10x speedup |

## Hot Paths (Optimization Priorities)

### 1. SVG Rendering (Highest ROI)
- **Bottleneck:** SVG→PNG conversion (cairosvg/ImageMagick)
- **Optimization:** Cache SVG templates; reuse converters
- **Expected gain:** 2-3x speedup (500ms → 200ms per frame)

### 2. Effects Pipeline (Medium ROI)
- **Bottleneck:** Per-frame PIL operations (resize, alpha blend)
- **Optimization:** Use NumPy for vectorized operations; pre-allocate buffers
- **Expected gain:** 1.5-2x speedup (200ms → 100ms per frame)

### 3. Screencast (Medium ROI)
- **Bottleneck:** FFmpeg subprocess overhead; frame extraction
- **Optimization:** Use FFmpeg pipes (stdin/stdout); reduce frame count
- **Expected gain:** 1.5x speedup (1000ms → 700ms per frame)

### 4. Blender (Low ROI for local, high for GPU)
- **Bottleneck:** CPU render; large scene complexity
- **Optimization:** GPU acceleration (CUDA/OptiX); defer to cloud render
- **Expected gain:** 10-100x with GPU (30s → 3s per frame)

## Benchmarking Command

```bash
python3 -m video_producer_skill_2_0.benchmarks.renderer_bench \
  --renderer svg \
  --frames 100 \
  --complexity high \
  --output benchmark_report.json
```

## Load Test (30 concurrent renders)

Target: All 4 renderers rendering simultaneously
- **Total throughput:** ~60 frames/s (1920x1080@30fps baseline)
- **Memory budget:** 2-4GB (Blender is memory-intensive)
- **CPU:** 4+ cores (Blender async execution)

## Future Optimizations

1. **Batching:** Render multiple scenes in parallel (ProcessPoolExecutor)
2. **GPU:** Blender CUDA, FFmpeg hardware acceleration
3. **Caching:** LRU cache for SVG templates, effect chains
4. **Streaming:** Frame pipes instead of disk I/O
5. **Cloud:** Delegate Blender to serverless (Kubernetes)
