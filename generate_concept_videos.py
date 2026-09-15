#!/usr/bin/env python3
"""
CorvinOS Concept Videos Generator
Generates 5 high-quality concept videos:
1. Organic Flow — Perlin-Noise Particles
2. Fractal Zoom — Mandelbrot-Set
3. Network Graph — Physics-based Nodes
4. Wave Interference — Overlapping Sine-Waves
5. Particle Physics — N-Body Simulation

Each video: 150 frames, 1920×1080, 25 fps (6 seconds), H.264 CRF 14
Narration: OpenAI TTS (tts-1-hd, nova voice)
"""

import os
import sys
import math
import json
import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter
from dataclasses import dataclass
from typing import List, Tuple
import struct
import hashlib

# Configuration
FRAMES = 150
WIDTH = 1920
HEIGHT = 1080
FPS = 25
OUTPUT_DIR = Path("/home/shumway/projects/CorvinOS/outputs")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

@dataclass
class Color:
    r: int
    g: int
    b: int

    def to_tuple(self) -> Tuple[int, int, int]:
        return (self.r, self.g, self.b)

    @staticmethod
    def from_hsl(h: float, s: float, l: float) -> 'Color':
        """Convert HSL to RGB (0-1 range inputs, 0-255 range output)"""
        c = (1 - abs(2 * l - 1)) * s
        x = c * (1 - abs((h * 6) % 2 - 1))
        m = l - c / 2

        if h < 1/6:
            r, g, b = c, x, 0
        elif h < 2/6:
            r, g, b = x, c, 0
        elif h < 3/6:
            r, g, b = 0, c, x
        elif h < 4/6:
            r, g, b = 0, x, c
        elif h < 5/6:
            r, g, b = x, 0, c
        else:
            r, g, b = c, 0, x

        return Color(
            int((r + m) * 255),
            int((g + m) * 255),
            int((b + m) * 255)
        )


class PerlinNoise:
    """Simple Perlin-like noise generator using gradients"""

    def __init__(self, seed: int = 42):
        self.seed = seed
        self.permutation = list(range(256))
        # Shuffle based on seed
        import random
        random.seed(seed)
        random.shuffle(self.permutation)
        self.p = self.permutation + self.permutation

    def noise(self, x: float, y: float = 0) -> float:
        """Generate noise value (0-1)"""
        xi = int(x) & 255
        yi = int(y) & 255

        xf = x - int(x)
        yf = y - int(y)

        # Fade function
        u = xf * xf * (3.0 - 2.0 * xf)
        v = yf * yf * (3.0 - 2.0 * yf)

        # Hash values
        aa = self.p[self.p[xi] + yi]
        ab = self.p[self.p[xi] + yi + 1]
        ba = self.p[self.p[xi + 1] + yi]
        bb = self.p[self.p[xi + 1] + yi + 1]

        # Interpolate
        x1 = (aa % 2) * xf + ((aa // 2) % 2) * (1 - xf)
        x2 = (ba % 2) * (xf - 1) + ((ba // 2) % 2) * (1 - xf + 1)
        y1 = (1 - v) * x1 + v * x2

        x1 = (ab % 2) * xf + ((ab // 2) % 2) * (1 - xf)
        x2 = (bb % 2) * (xf - 1) + ((bb // 2) % 2) * (1 - xf + 1)
        y2 = (1 - v) * x1 + v * x2

        return (1 - u) * y1 + u * y2


class VideoGenerator:
    def __init__(self, name: str, narration: str):
        self.name = name
        self.narration = narration
        self.frames = []
        self.output_path = OUTPUT_DIR / f"{name}.mp4"

    def generate_frame(self, frame_num: int) -> Image.Image:
        """Override in subclass"""
        raise NotImplementedError

    def generate_frames(self):
        """Generate all frames"""
        print(f"Generating {FRAMES} frames for {self.name}...")
        for i in range(FRAMES):
            if (i + 1) % 30 == 0:
                print(f"  Frame {i + 1}/{FRAMES}")
            frame = self.generate_frame(i)
            self.frames.append(frame)

    def generate_audio(self) -> str:
        """Generate TTS audio using OpenAI API"""
        if not OPENAI_API_KEY:
            print(f"⚠️  No OPENAI_API_KEY set. Skipping audio for {self.name}")
            return None

        print(f"Generating TTS audio for {self.name}...")
        import urllib.request
        import json

        try:
            url = "https://api.openai.com/v1/audio/speech"
            headers = {
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            }
            data = {
                "model": "tts-1-hd",
                "input": self.narration,
                "voice": "nova"
            }

            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode('utf-8'),
                headers=headers,
                method='POST'
            )

            with urllib.request.urlopen(req) as response:
                audio_path = OUTPUT_DIR / f"{self.name}_audio.mp3"
                with open(audio_path, 'wb') as f:
                    f.write(response.read())
                print(f"  ✅ Audio saved to {audio_path}")
                return str(audio_path)

        except Exception as e:
            print(f"  ❌ TTS generation failed: {e}")
            return None

    def encode_video(self, audio_path: str = None):
        """Encode frames to video with audio"""
        print(f"Encoding video for {self.name}...")

        # Create temporary frame file list
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            for i, frame in enumerate(self.frames):
                frame_path = OUTPUT_DIR / f"frame_{self.name}_{i:04d}.png"
                frame.save(frame_path)
                f.write(f"file '{frame_path}'\n")
                f.write(f"duration 0.04\n")  # 1/25 = 0.04s per frame
            frame_list = f.name

        try:
            # Create video from frames
            cmd = [
                'ffmpeg', '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', frame_list,
                '-c:v', 'libx264',
                '-crf', '14',
                '-pix_fmt', 'yuv420p',
                '-r', str(FPS),
                '-s', f'{WIDTH}x{HEIGHT}',
                OUTPUT_DIR / f"{self.name}_novideo.mp4"
            ]
            subprocess.run(cmd, check=True, capture_output=True)

            # Add audio if available
            if audio_path:
                cmd = [
                    'ffmpeg', '-y',
                    '-i', OUTPUT_DIR / f"{self.name}_novideo.mp4",
                    '-i', audio_path,
                    '-c:v', 'copy',
                    '-c:a', 'aac',
                    '-shortest',
                    str(self.output_path)
                ]
                subprocess.run(cmd, check=True, capture_output=True)
                # Clean up
                (OUTPUT_DIR / f"{self.name}_novideo.mp4").unlink()
                print(f"  ✅ Video with audio: {self.output_path}")
            else:
                (OUTPUT_DIR / f"{self.name}_novideo.mp4").rename(self.output_path)
                print(f"  ✅ Video (no audio): {self.output_path}")

        finally:
            # Clean up temporary files
            os.unlink(frame_list)
            for i in range(FRAMES):
                frame_path = OUTPUT_DIR / f"frame_{self.name}_{i:04d}.png"
                if frame_path.exists():
                    frame_path.unlink()


class OrganicFlowVideo(VideoGenerator):
    """Perlin-Noise based particle animation"""

    def __init__(self):
        narration = (
            "Organic Flow — flowing liquid-like motion guided by noise-based particle systems. "
            "Each particle moves through a field of invisible forces, creating natural and graceful "
            "movement patterns that emerge from simple mathematical rules. Watch as individual paths "
            "weave together into a cohesive, organic whole — never repeating, always flowing. "
            "This is how systems organize themselves through continuous, fluid adaptation."
        )
        super().__init__("03_CorvinOS_Organic_Flow", narration)
        self.noise = PerlinNoise(seed=42)

    def generate_frame(self, frame_num: int) -> Image.Image:
        img = Image.new('RGB', (WIDTH, HEIGHT), (20, 25, 35))
        draw = ImageDraw.Draw(img, 'RGBA')

        # Draw gradient background
        for y in range(HEIGHT):
            h = 0.6 + (y / HEIGHT) * 0.1  # Hue shift top to bottom
            s = 0.3 + (y / HEIGHT) * 0.4  # Saturation increases downward
            l = 0.15 + (y / HEIGHT) * 0.1  # Lightness increases downward
            color = Color.from_hsl(h, s, l)
            draw.line([(0, y), (WIDTH, y)], fill=color.to_tuple())

        # Generate particles
        time = frame_num / FRAMES
        num_particles = 300

        for p in range(num_particles):
            seed = p
            x = ((p % 20) / 20.0) * WIDTH
            y = ((p // 20) / 15.0) * HEIGHT

            # Noise-based offset
            noise_x = self.noise.noise(x / 300 + time * 2, y / 300) * 100 - 50
            noise_y = self.noise.noise(x / 300, y / 300 + time * 2) * 100 - 50

            x = (x + noise_x) % WIDTH
            y = (y + noise_y) % HEIGHT

            # Color based on position and time
            h = (x / WIDTH + time * 0.2) % 1.0
            s = 0.6 + 0.3 * self.noise.noise(p / 100, time)
            l = 0.5 + 0.2 * self.noise.noise(p / 50, time + 1)

            color = Color.from_hsl(h, s, l)
            alpha = int(180 * (0.5 + 0.5 * self.noise.noise(p / 30, time)))

            # Draw particle with glow
            size = 3
            draw.ellipse([x-size, y-size, x+size, y+size],
                        fill=color.to_tuple() + (alpha,))

        return img


class FractalZoomVideo(VideoGenerator):
    """Mandelbrot-set with animated zoom"""

    def __init__(self):
        narration = (
            "Fractal Zoom — self-similar patterns at multiple scales. "
            "The Mandelbrot set reveals a universe of infinite complexity within a finite space. "
            "As we zoom deeper, we discover the same patterns repeating, fractal upon fractal, "
            "a never-ending dance of order and chaos. This is the geometry of emergence — "
            "simple rules generating boundless beauty."
        )
        super().__init__("04_CorvinOS_Fractal_Zoom", narration)

    def mandelbrot(self, x: float, y: float, max_iter: int = 100) -> int:
        """Calculate Mandelbrot iteration count"""
        c = complex(x, y)
        z = 0j
        for n in range(max_iter):
            if abs(z) > 2:
                return n
            z = z * z + c
        return max_iter

    def generate_frame(self, frame_num: int) -> Image.Image:
        img = Image.new('RGB', (WIDTH, HEIGHT))
        pixels = img.load()

        # Zoom animation (5x zoom over 150 frames)
        zoom_factor = 1.0 + (frame_num / FRAMES) ** 1.5 * 4.0
        center_x = -0.7  # Interesting point
        center_y = 0.27015

        scale = 2.0 / zoom_factor / HEIGHT

        for y in range(HEIGHT):
            for x in range(WIDTH):
                # Map pixel to complex plane
                real = center_x + (x - WIDTH / 2) * scale
                imag = center_y + (y - HEIGHT / 2) * scale

                # Calculate iterations
                iterations = self.mandelbrot(real, imag)

                # Color mapping with harmonics
                hue = (iterations / 100.0) % 1.0
                saturation = 0.7 + 0.3 * math.sin(iterations * 0.1)
                lightness = 0.3 + 0.4 * (iterations / 100.0)

                color = Color.from_hsl(hue, saturation, lightness)
                pixels[x, y] = color.to_tuple()

                if y % 100 == 0:
                    pass  # Progress

        return img


class NetworkGraphVideo(VideoGenerator):
    """Physics-based node network visualization"""

    def __init__(self):
        narration = (
            "Network Graph — nodes connected by invisible forces. "
            "Attraction pulls similar nodes together; repulsion keeps them apart. "
            "Demonstrating how systems organize themselves through simple local rules. "
            "No central planner, no global blueprint — just individual forces creating emergent order. "
            "This is self-organization in action."
        )
        super().__init__("05_CorvinOS_Network_Graph", narration)
        self.initialize_nodes()

    def initialize_nodes(self):
        """Initialize node positions and velocities"""
        self.nodes = []
        num_nodes = 10
        for i in range(num_nodes):
            x = (i % 5) / 5.0 * WIDTH + 200
            y = (i // 5) / 2.0 * HEIGHT + 200
            vx = (i * 0.3) % 2 - 1
            vy = (i * 0.5) % 2 - 1
            color_h = i / num_nodes
            self.nodes.append({
                'x': x, 'y': y,
                'vx': vx, 'vy': vy,
                'hue': color_h,
                'mass': 1.0 + (i % 3) * 0.3
            })

        # Define connections
        self.connections = [
            (0, 1), (1, 2), (2, 3), (3, 4),
            (5, 6), (6, 7), (7, 8), (8, 9),
            (0, 5), (2, 7), (4, 9)
        ]

    def generate_frame(self, frame_num: int) -> Image.Image:
        img = Image.new('RGB', (WIDTH, HEIGHT), (20, 25, 35))
        draw = ImageDraw.Draw(img, 'RGBA')

        # Physics simulation step
        time = frame_num / FRAMES

        for i, node in enumerate(self.nodes):
            fx, fy = 0, 0

            # Repulsion from other nodes
            for j, other in enumerate(self.nodes):
                if i != j:
                    dx = node['x'] - other['x']
                    dy = node['y'] - other['y']
                    dist = math.sqrt(dx*dx + dy*dy) + 1

                    force = 50000 / (dist * dist)
                    fx += dx / dist * force
                    fy += dy / dist * force

            # Attraction to connected nodes
            for a, b in self.connections:
                if i == a:
                    other = self.nodes[b]
                elif i == b:
                    other = self.nodes[a]
                else:
                    continue

                dx = other['x'] - node['x']
                dy = other['y'] - node['y']
                dist = math.sqrt(dx*dx + dy*dy) + 1

                force = dist * 0.02
                fx += dx / dist * force
                fy += dy / dist * force

            # Damping
            node['vx'] = (node['vx'] + fx / node['mass']) * 0.95
            node['vy'] = (node['vy'] + fy / node['mass']) * 0.95

            # Boundaries
            node['x'] += node['vx']
            node['y'] += node['vy']

            if node['x'] < 50:
                node['x'] = 50
                node['vx'] = -node['vx'] * 0.5
            if node['x'] > WIDTH - 50:
                node['x'] = WIDTH - 50
                node['vx'] = -node['vx'] * 0.5
            if node['y'] < 50:
                node['y'] = 50
                node['vy'] = -node['vy'] * 0.5
            if node['y'] > HEIGHT - 50:
                node['y'] = HEIGHT - 50
                node['vy'] = -node['vy'] * 0.5

        # Draw connections
        for a, b in self.connections:
            node_a = self.nodes[a]
            node_b = self.nodes[b]

            color = Color.from_hsl(0.5, 0.3, 0.4)
            draw.line([
                (node_a['x'], node_a['y']),
                (node_b['x'], node_b['y'])
            ], fill=color.to_tuple() + (100,), width=2)

        # Draw nodes
        for i, node in enumerate(self.nodes):
            color = Color.from_hsl(node['hue'], 0.8, 0.5)
            size = 8 * node['mass']
            draw.ellipse([
                node['x'] - size,
                node['y'] - size,
                node['x'] + size,
                node['y'] + size
            ], fill=color.to_tuple())

            # Glow effect
            draw.ellipse([
                node['x'] - size - 3,
                node['y'] - size - 3,
                node['x'] + size + 3,
                node['y'] + size + 3
            ], outline=color.to_tuple() + (80,), width=2)

        return img


class WaveInterferenceVideo(VideoGenerator):
    """Overlapping sine-waves creating interference patterns"""

    def __init__(self):
        narration = (
            "Wave Interference — when multiple oscillations overlap, they create interference patterns "
            "that reveal the underlying harmony. Peaks amplify, valleys cancel, and in the spaces between, "
            "a new order emerges. This is the mathematics of resonance — "
            "a fundamental principle of how systems amplify or dampen each other's effects."
        )
        super().__init__("06_CorvinOS_Wave_Interference", narration)

    def generate_frame(self, frame_num: int) -> Image.Image:
        img = Image.new('RGB', (WIDTH, HEIGHT), (15, 20, 30))
        pixels = img.load()

        time = frame_num / FRAMES

        # Wave parameters
        freq1, freq2, freq3 = 0.01, 0.015, 0.008
        phase1 = time * 2 * math.pi * 0.5
        phase2 = time * 2 * math.pi * 0.3 + math.pi / 3
        phase3 = time * 2 * math.pi * 0.2 + math.pi

        for y in range(HEIGHT):
            for x in range(WIDTH):
                # Three overlapping waves
                w1 = math.sin(x * freq1 + phase1) * math.cos(y * freq1 * 0.5 + phase1)
                w2 = math.sin(x * freq2 + phase2) * math.sin(y * freq2 * 0.5 + phase2)
                w3 = math.sin(x * freq3 + phase3) * math.cos(y * freq3 * 0.5 + phase3)

                # Combined amplitude
                amplitude = (w1 + w2 + w3) / 3.0

                # Color based on interference
                hue = (0.5 + amplitude * 0.3) % 1.0
                saturation = 0.6 + 0.3 * abs(amplitude)
                lightness = 0.3 + 0.4 * (amplitude + 1) / 2.0

                color = Color.from_hsl(hue, saturation, lightness)
                pixels[x, y] = color.to_tuple()

        return img


class ParticlePhysicsVideo(VideoGenerator):
    """N-Body simulation with gravity and repulsion"""

    def __init__(self):
        narration = (
            "Particle Physics — massless and massive particles interact through fundamental forces. "
            "Gravity pulls them together; repulsion keeps them apart. "
            "Trails trace the paths of their dance. Watch as simple interactions create dynamic, emergent patterns "
            "that no single particle could predict. This is complexity from simplicity."
        )
        super().__init__("07_CorvinOS_Particle_Physics", narration)
        self.initialize_particles()

    def initialize_particles(self):
        """Initialize particles with random positions and velocities"""
        self.particles = []
        self.trails = []
        num_particles = 25

        import random
        random.seed(42)

        for i in range(num_particles):
            x = random.uniform(100, WIDTH - 100)
            y = random.uniform(100, HEIGHT - 100)
            vx = random.uniform(-2, 2)
            vy = random.uniform(-2, 2)
            mass = 1.0 + random.random() * 2.0
            hue = random.random()

            self.particles.append({
                'x': x, 'y': y,
                'vx': vx, 'vy': vy,
                'mass': mass,
                'hue': hue,
                'history': []
            })
            self.trails.append([])

    def generate_frame(self, frame_num: int) -> Image.Image:
        img = Image.new('RGB', (WIDTH, HEIGHT), (15, 20, 30))
        draw = ImageDraw.Draw(img, 'RGBA')

        # Physics update
        dt = 0.016  # ~60fps equivalent

        for i, particle in enumerate(self.particles):
            fx, fy = 0, 0

            for j, other in enumerate(self.particles):
                if i == j:
                    continue

                dx = other['x'] - particle['x']
                dy = other['y'] - particle['y']
                dist = math.sqrt(dx*dx + dy*dy) + 1

                # Gravity (attractive)
                gravity = 0.1 * particle['mass'] * other['mass'] / (dist * dist)
                fx += dx / dist * gravity
                fy += dy / dist * gravity

                # Repulsion (short-range)
                if dist < 100:
                    repulsion = 10000 / (dist * dist)
                    fx -= dx / dist * repulsion
                    fy -= dy / dist * repulsion

            # Damping
            fx *= 0.98
            fy *= 0.98

            # Update velocity and position
            particle['vx'] += fx * dt
            particle['vy'] += fy * dt
            particle['x'] += particle['vx']
            particle['y'] += particle['vy']

            # Wrap around boundaries
            if particle['x'] < 0:
                particle['x'] = WIDTH
            elif particle['x'] > WIDTH:
                particle['x'] = 0

            if particle['y'] < 0:
                particle['y'] = HEIGHT
            elif particle['y'] > HEIGHT:
                particle['y'] = 0

            # Store history for trails
            self.trails[i].append((particle['x'], particle['y']))
            if len(self.trails[i]) > 30:
                self.trails[i].pop(0)

        # Draw trails
        for i, trail in enumerate(self.trails):
            particle = self.particles[i]
            color = Color.from_hsl(particle['hue'], 0.7, 0.5)

            for j in range(1, len(trail)):
                alpha = int(50 + 150 * (j / len(trail)))
                draw.line([trail[j-1], trail[j]], fill=color.to_tuple() + (alpha,), width=1)

        # Draw particles
        for i, particle in enumerate(self.particles):
            color = Color.from_hsl(particle['hue'], 0.8, 0.5)
            size = 4 + particle['mass']

            draw.ellipse([
                particle['x'] - size,
                particle['y'] - size,
                particle['x'] + size,
                particle['y'] + size
            ], fill=color.to_tuple())

            # Glow
            draw.ellipse([
                particle['x'] - size - 2,
                particle['y'] - size - 2,
                particle['x'] + size + 2,
                particle['y'] + size + 2
            ], outline=color.to_tuple() + (100,), width=1)

        return img


def main():
    """Generate all 5 concept videos"""

    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 80)
    print("CorvinOS Concept Videos Generator")
    print("=" * 80)
    print(f"Output directory: {OUTPUT_DIR}")
    print(f"Configuration: {FRAMES} frames, {WIDTH}×{HEIGHT}, {FPS} fps, H.264 CRF 14")
    print()

    # Create video generators
    generators = [
        OrganicFlowVideo(),
        FractalZoomVideo(),
        NetworkGraphVideo(),
        WaveInterferenceVideo(),
        ParticlePhysicsVideo(),
    ]

    # Generate each video
    for gen in generators:
        print(f"\n{'='*80}")
        print(f"Generating: {gen.name}")
        print(f"{'='*80}")

        try:
            # Generate frames
            gen.generate_frames()

            # Generate audio
            audio_path = gen.generate_audio()

            # Encode video
            gen.encode_video(audio_path)

            # Cleanup audio file
            if audio_path and os.path.exists(audio_path):
                os.unlink(audio_path)

            print(f"✅ {gen.name} complete\n")

        except Exception as e:
            print(f"❌ Error generating {gen.name}: {e}\n")
            import traceback
            traceback.print_exc()

    print(f"\n{'='*80}")
    print("All videos complete!")
    print(f"Location: {OUTPUT_DIR}")
    print("=" * 80)

    # Verify videos
    for gen in generators:
        if gen.output_path.exists():
            size_mb = gen.output_path.stat().st_size / (1024 * 1024)
            print(f"✅ {gen.output_path.name} ({size_mb:.1f} MB)")
        else:
            print(f"❌ {gen.output_path.name} NOT FOUND")


if __name__ == "__main__":
    main()
