/**
 * Makise Kurisu PixiJS Sprite & Animation Engine (Phase 5 Refined)
 * Target: Smooth 60 FPS on GPU/CPU, 1:1 pixel-perfect framing, procedural breathing,
 *         Poisson-distributed blinking (with double-blinks), blush preservation,
 *         smooth cross-dissolve emotion blending, and real-time audio visemes.
 */
import { Application, Assets, Sprite, Container, Graphics, Texture } from "pixi.js";

export class KurisuStage {
  constructor(canvasContainer) {
    this.container = canvasContainer;
    this.app = null;
    this.manifest = null;
    this.spritesDir = null;
    this.spritesAvailable = true;

    // Steins;Gate 0 Digital Server Void Backdrop Elements
    this.bgContainer = null;
    this.baseGfx = null;
    this.gridGfx = null;
    this.scanlinesGfx = null;
    this.motesContainer = null;
    this.motes = [];
    this.dataTraceContainer = null;
    this.dataColumns = [];
    this.traceFlickerTimer = 0;

    // Containers & Sprites for Cross-Dissolving
    this.stageContainer = null;
    this.primarySprite = null;
    this.secondarySprite = null;

    // Active state
    this.currentEmotion = "neutral";
    this.currentPose = "D";
    this.currentMouth = "closed"; // "closed" (00), "half" (01), "open" (02)
    this.isBlinking = false;
    this.blinkEnabled = true;
    this.blinkTimer = null;

    // Procedural Breathing parameters
    this.breathTime = 0;
    this.baseScale = 1.0;
    this.breathingEnabled = true;

    // Cross-fade animation state
    this.isTransitioning = false;
    this.transitionProgress = 1.0;
    this.transitionDuration = 0.16; // 160ms cross-dissolve

    // Texture cache
    this.loadedTextures = new Map();

    // Telemetry callback
    this.onStateChange = null;
  }

  resolveSpriteUrl(filename) {
    return `/avatar/sprites/${filename}`;
  }

  async init() {
    // 1. Initialize PixiJS Application
    this.app = new Application();
    await this.app.init({
      backgroundAlpha: 0,
      resizeTo: this.container,
      resolution: window.devicePixelRatio || 1,
      autoDensity: true,
      preference: "webgl",
    });

    this.container.appendChild(this.app.canvas);

    // 2. Setup container hierarchy
    this.bgContainer = new Container();
    this.app.stage.addChild(this.bgContainer);
    this.setupServerVoidBackdrop();

    this.stageContainer = new Container();
    this.app.stage.addChild(this.stageContainer);

    // Primary sprite (active display)
    this.primarySprite = new Sprite();
    this.primarySprite.anchor.set(0.5, 1.0); // Center X, bottom Y
    this.stageContainer.addChild(this.primarySprite);

    // Secondary sprite (used for smooth cross-dissolve fades)
    this.secondarySprite = new Sprite();
    this.secondarySprite.anchor.set(0.5, 1.0);
    this.secondarySprite.alpha = 0;
    this.stageContainer.addChild(this.secondarySprite);

    this.resize();

    // 3. Load bundled Kurisu sprite manifest and preload textures
    try {
      const res = await fetch("/avatar/manifest.json");
      this.manifest = await res.json();
      await this.preloadAllTextures();
      const initialTexture = this.getTexture("neutral", "closed");
      if (initialTexture && this.primarySprite) {
        this.primarySprite.texture = initialTexture;
        this.primarySprite.alpha = 1;
        this.resize();
      }
    } catch (e) {
      console.warn("[Avatar] Preload error:", e);
    }

    // 4. Hook ticker loop for procedural breathing & cross-dissolves
    this.app.ticker.add((ticker) => this.onTick(ticker));

    // 5. Handle window resize
    window.addEventListener("resize", () => this.resize());

    // 6. Start Poisson-distributed blinking loop
    this.schedulePoissonBlink();
  }

  async loadAvatarConfig(config) {
    if (!config) return;
    if (config.manifest) {
      this.manifest = config.manifest;
    }
    if (!this.loadedTextures.size && this.manifest) {
      await this.preloadAllTextures();
      const initialTexture = this.getTexture("neutral", "closed");
      if (initialTexture && this.primarySprite) {
        this.primarySprite.texture = initialTexture;
        this.primarySprite.alpha = 1;
        this.resize();
      }
    }
  }

  setupServerVoidBackdrop() {
    // 1. Base Gradient & Subtle Radial Glow Graphics
    this.baseGfx = new Graphics();
    this.bgContainer.addChild(this.baseGfx);

    // 2. Orthogonal Dark-Teal Grid
    this.gridGfx = new Graphics();
    this.bgContainer.addChild(this.gridGfx);

    // 3. Floating Bokeh Motes
    this.motesContainer = new Container();
    this.bgContainer.addChild(this.motesContainer);

    const moteColors = [0x00f0ff, 0x00e5a3, 0x0088cc, 0x00ffcc, 0x10b981];
    this.motes = [];
    const moteCount = 22;
    for (let i = 0; i < moteCount; i++) {
      const g = new Graphics();
      const radius = 2.0 + Math.random() * 4.5;
      const col = moteColors[Math.floor(Math.random() * moteColors.length)];
      g.circle(0, 0, radius).fill({ color: col, alpha: 0.85 });
      g.circle(0, 0, radius * 2.2).fill({ color: col, alpha: 0.18 });
      g.blendMode = "add";
      this.motesContainer.addChild(g);

      this.motes.push({
        gfx: g,
        x: Math.random() * 800,
        y: Math.random() * 1000,
        radius,
        speedY: 0.25 + Math.random() * 0.45,
        driftX: (Math.random() - 0.5) * 0.12,
        pulsePhase: Math.random() * Math.PI * 2,
        pulseSpeed: 1.0 + Math.random() * 1.6,
        baseAlpha: 0.25 + Math.random() * 0.45,
      });
    }

    // 4. Vertical Data Trace Bars (3-5 columns of emerald tick marks in margins)
    this.dataTraceContainer = new Container();
    this.bgContainer.addChild(this.dataTraceContainer);
    this.dataColumns = [];
    const colCount = 4;
    for (let c = 0; c < colCount; c++) {
      const colGfx = new Graphics();
      this.dataTraceContainer.addChild(colGfx);
      this.dataColumns.push({
        gfx: colGfx,
        colIndex: c,
        x: 0,
        ticks: Array.from({ length: 24 }, () => ({
          alpha: 0.1 + Math.random() * 0.45,
          color: Math.random() > 0.3 ? 0x00ff66 : 0x10e070,
        })),
      });
    }

    // 5. Scanlines Graphics (1px dark lines, 25% opacity, 3px spacing)
    this.scanlinesGfx = new Graphics();
    this.bgContainer.addChild(this.scanlinesGfx);
  }

  renderDataTraceColumns(screenHeight) {
    if (!this.dataColumns) return;
    const startY = 40;
    const tickHeight = 6;
    const tickWidth = 2;
    const gap = 3;

    this.dataColumns.forEach((col) => {
      col.gfx.clear();
      col.ticks.forEach((t, i) => {
        const y = startY + i * (tickHeight + gap);
        if (y < screenHeight - 60) {
          col.gfx.rect(col.x, y, tickWidth, tickHeight).fill({ color: t.color, alpha: t.alpha });
        }
      });
    });
  }

  async preloadAllTextures() {
    if (!this.manifest || !this.manifest.emotions) return;
    const promises = [];
    const loadSingleTexture = (filename, isBlink = false) => {
      if (this.loadedTextures.has(filename)) return;
      const url = this.resolveSpriteUrl(filename);
      promises.push(
        Assets.load(url)
          .then((tex) => this.loadedTextures.set(filename, tex))
          .catch(async () => {
            try {
              const tex = await new Promise((resolve, reject) => {
                const img = new Image();
                img.onload = () => resolve(Texture.from(img));
                img.onerror = reject;
                img.src = url;
              });
              this.loadedTextures.set(filename, tex);
            } catch (err) {
              console.warn(`[Avatar] Failed to load ${isBlink ? "blink " : ""}frame:`, filename, err);
            }
          })
      );
    };

    for (const [emKey, em] of Object.entries(this.manifest.emotions)) {
      if (em.frames) {
        for (const [mState, filename] of Object.entries(em.frames)) {
          loadSingleTexture(filename, false);
        }
      }
      if (em.blinkFrames) {
        for (const [mState, filename] of Object.entries(em.blinkFrames)) {
          loadSingleTexture(filename, true);
        }
      }
    }
    await Promise.all(promises);
  }

  getTexture(emotionKey, mouthState) {
    if (!this.manifest || !this.manifest.emotions) return null;
    const em = this.manifest.emotions[emotionKey] || this.manifest.emotions.neutral;
    if (!em || !em.frames) return null;
    const filename = em.frames[mouthState] || em.frames.closed || "CRS_JMD_40000100.png";
    return this.loadedTextures.get(filename);
  }

  getBlinkTexture(emotionKey, mouthState) {
    if (!this.manifest || !this.manifest.emotions) return null;
    const em = this.manifest.emotions[emotionKey];
    if (em && em.blinkFrames) {
      const filename = em.blinkFrames[mouthState] || em.blinkFrames.closed;
      if (filename) {
        const tex = this.loadedTextures.get(filename);
        if (tex) return tex;
      }
    }
    const closedEyeEmotion = this.currentPose === "E" ? "dismissive_hmph" : "thinking";
    return this.getTexture(closedEyeEmotion, mouthState);
  }

  // --- Procedural Ticker: Breathing & Cross-Dissolve ---
  onTick(ticker) {
    const deltaSeconds = ticker.deltaTime / 60;

    // 1. Procedural Living Breathing Motion
    if (this.breathingEnabled && this.baseScale > 0) {
      this.breathTime += deltaSeconds * 1.65; // ~3.8 second human respiratory cycle
      const breathCycle = Math.sin(this.breathTime);

      // Subtle 0.35% vertical chest expansion
      const breathY = 1.0 + breathCycle * 0.0035;
      // Slight 0.12% transverse lateral contraction (Poisson ratio)
      const breathX = 1.0 - breathCycle * 0.0012;

      const scaleX = this.baseScale * breathX;
      const scaleY = this.baseScale * breathY;

      this.primarySprite.scale.set(scaleX, scaleY);
      this.secondarySprite.scale.set(scaleX, scaleY);
    }

    // 2. Smooth Cross-Dissolve Between Emotions
    if (this.isTransitioning) {
      this.transitionProgress += deltaSeconds / this.transitionDuration;
      if (this.transitionProgress >= 1.0) {
        this.transitionProgress = 1.0;
        this.isTransitioning = false;

        // Finalize swap: primary holds the new texture at full opacity
        this.primarySprite.texture = this.secondarySprite.texture;
        this.primarySprite.alpha = 1.0;
        this.secondarySprite.alpha = 0.0;
        this.transitionDuration = 0.16; // restore default
      } else {
        // Smooth ease-in-out cosine cross-dissolve (graceful fade without abrupt edges)
        const t = this.transitionProgress;
        const smoothT = 0.5 - 0.5 * Math.cos(t * Math.PI);
        this.primarySprite.alpha = 1.0 - smoothT;
        this.secondarySprite.alpha = smoothT;
      }
    }

    // 3. Animate Floating Bokeh Motes
    if (this.app && this.motes && this.motes.length > 0) {
      const screenWidth = this.app.screen.width;
      const screenHeight = this.app.screen.height;

      for (const mote of this.motes) {
        mote.y -= mote.speedY * deltaSeconds * 60;
        mote.x += mote.driftX * deltaSeconds * 60;
        mote.pulsePhase += mote.pulseSpeed * deltaSeconds;

        if (mote.y < -20) {
          mote.y = screenHeight + 20;
          mote.x = Math.random() * screenWidth;
        }
        if (mote.x < -10) mote.x = screenWidth + 10;
        else if (mote.x > screenWidth + 10) mote.x = -10;

        mote.gfx.x = mote.x;
        mote.gfx.y = mote.y;
        mote.gfx.alpha = mote.baseAlpha * (0.6 + 0.4 * Math.sin(mote.pulsePhase));
      }

      // 4. Stepped Alpha Flickering for Emerald Data Trace Bars
      this.traceFlickerTimer += deltaSeconds;
      if (this.traceFlickerTimer >= 0.14) {
        this.traceFlickerTimer = 0;
        for (const col of this.dataColumns) {
          for (const t of col.ticks) {
            if (Math.random() < 0.25) {
              t.alpha = 0.08 + Math.random() * 0.52;
            }
          }
        }
        this.renderDataTraceColumns(screenHeight);
      }
    }
  }

  // --- Emotion & Pose Transition with Cross-Dissolve ---
  setEmotion(emotionKey, duration = null) {
    if (!this.manifest.emotions[emotionKey]) {
      console.warn("Unknown emotion:", emotionKey);
      return;
    }

    if (this.currentEmotion === emotionKey) return;

    const prevEmotion = this.currentEmotion;
    this.currentEmotion = emotionKey;
    this.currentPose = this.manifest.emotions[emotionKey].pose || "D";

    // Set transition duration (custom or default 0.16s)
    this.transitionDuration = (typeof duration === "number" && duration > 0) ? duration : 0.16;

    // Begin cross-dissolve to new emotion
    const newTexture = this.getTexture(this.currentEmotion, this.currentMouth);
    if (newTexture && newTexture !== this.primarySprite.texture) {
      if (this.isTransitioning && this.secondarySprite.texture) {
        this.primarySprite.texture = this.secondarySprite.texture;
        this.primarySprite.alpha = 1.0;
      }
      this.secondarySprite.texture = newTexture;
      this.secondarySprite.alpha = 0.0;
      this.transitionProgress = 0.0;
      this.isTransitioning = true;
    }

    this.notifyState();
  }

  // --- Viseme / Mouth Flap Swapping (Instant & Razor-Sharp) ---
  setMouthState(mouthState) {
    if (this.currentMouth === mouthState) return;
    this.currentMouth = mouthState;

    if (!this.isBlinking) {
      const tex = this.getTexture(this.currentEmotion, this.currentMouth);
      if (tex) {
        this.primarySprite.texture = tex;
      }
    }

    this.notifyState();
  }

  setAudioRms(rms) {
    let targetMouth = "closed";
    if (rms >= 0.075) {
      targetMouth = "open";
    } else if (rms >= 0.02) {
      targetMouth = "half";
    } else {
      targetMouth = "closed";
    }
    this.setMouthState(targetMouth);
  }

  // --- Poisson-Distributed Natural Blinking ---
  schedulePoissonBlink() {
    this.clearBlinkTimer();
    if (!this.blinkEnabled) return;

    // Poisson process inter-arrival time: t = -lambda * ln(U)
    // Mean interval lambda = 3.8s, clamped between 1.2s and 6.5s
    const u = Math.max(0.0001, Math.random());
    const lambda = 3800; // 3.8 seconds mean
    let intervalMs = -lambda * Math.log(u);
    intervalMs = Math.min(6500, Math.max(1200, intervalMs));

    this.blinkTimer = setTimeout(() => {
      this.executeBlink();
    }, intervalMs);
  }

  clearBlinkTimer() {
    if (this.blinkTimer) {
      clearTimeout(this.blinkTimer);
      this.blinkTimer = null;
    }
  }

  executeBlink() {
    // Suppress blinking in emotions where eyes are already closed, winking, or turned away:
    // - warm: gentle closed smile (06)
    // - thinking: contemplative closed eyes (0a)
    // - teasing: playful one-eyed wink (02) - blinking breaks the wink!
    // - dismissive_hmph: closed eyes sigh (Pose E 07)
    // - back_turned: facing completely away (Pose F)
    // Note: flustered, tsundere, and distressed have dedicated eye-masked blink frames!
    const suppressBlink = [
      "warm",
      "thinking",
      "teasing",
      "dismissive_hmph",
      "back_turned",
    ].includes(this.currentEmotion);

    if (suppressBlink || !this.blinkEnabled) {
      this.schedulePoissonBlink();
      return;
    }

    const blinkTexture = this.getBlinkTexture(this.currentEmotion, this.currentMouth);
    if (!blinkTexture) {
      this.schedulePoissonBlink();
      return;
    }

    this.isBlinking = true;
    this.primarySprite.texture = blinkTexture;

    // Authentic single blink duration: ~125 ms
    const blinkDuration = 125;
    setTimeout(() => {
      // Restore active emotion texture
      const restoreTexture = this.getTexture(this.currentEmotion, this.currentMouth);
      if (restoreTexture) {
        this.primarySprite.texture = restoreTexture;
      }
      this.isBlinking = false;

      // Authentic biological double-blink check (~14% chance of immediate second micro-blink)
      const hasDoubleBlink = Math.random() < 0.14;
      if (hasDoubleBlink) {
        setTimeout(() => {
          this.executeMicroBlink();
        }, 90);
      } else {
        this.schedulePoissonBlink();
      }
    }, blinkDuration);
  }

  executeMicroBlink() {
    const blinkTexture = this.getBlinkTexture(this.currentEmotion, this.currentMouth);
    if (blinkTexture) {
      this.isBlinking = true;
      this.primarySprite.texture = blinkTexture;
      setTimeout(() => {
        const restoreTexture = this.getTexture(this.currentEmotion, this.currentMouth);
        if (restoreTexture) {
          this.primarySprite.texture = restoreTexture;
        }
        this.isBlinking = false;
        this.schedulePoissonBlink();
      }, 95);
    } else {
      this.schedulePoissonBlink();
    }
  }

  toggleBlinking(enable) {
    this.blinkEnabled = enable !== undefined ? enable : !this.blinkEnabled;
    if (this.blinkEnabled) {
      this.schedulePoissonBlink();
    } else {
      this.clearBlinkTimer();
      this.isBlinking = false;
      const restoreTexture = this.getTexture(this.currentEmotion, this.currentMouth);
      if (restoreTexture) {
        this.primarySprite.texture = restoreTexture;
      }
    }
    return this.blinkEnabled;
  }

  // --- 1:1 Pixel-Perfect Centering & Scaling ---
  resize() {
    if (!this.app || !this.primarySprite) return;

    const screenWidth = this.app.screen.width;
    const screenHeight = this.app.screen.height;

    // 1. Redraw Base Gradient & Subtle Radial Glow
    if (this.baseGfx) {
      this.baseGfx.clear();
      // Deep midnight petrol teal (#040d12)
      this.baseGfx.rect(0, 0, screenWidth, screenHeight).fill({ color: 0x040d12 });
      // Subtle radial glow behind Kurisu (#0a2228 to #030709)
      const centerX = screenWidth * 0.5;
      const centerY = screenHeight * 0.42;
      const glowSteps = 12;
      const maxRadius = Math.max(screenWidth, screenHeight) * 0.65;
      for (let s = glowSteps; s >= 1; s--) {
        const r = (maxRadius / glowSteps) * s;
        const alpha = 0.018 * (1.0 - (s / glowSteps) * 0.7);
        this.baseGfx.circle(centerX, centerY, r).fill({ color: 0x0a2228, alpha });
      }
      this.baseGfx.circle(centerX, centerY, maxRadius * 0.28).fill({ color: 0x00f0ff, alpha: 0.035 });
    }

    // 2. Redraw Orthogonal Grid (48px spacing, dark-teal rgba(0, 240, 255, 0.055))
    if (this.gridGfx) {
      this.gridGfx.clear();
      const gridStep = 48;
      for (let x = 0; x <= screenWidth; x += gridStep) {
        this.gridGfx.moveTo(x, 0).lineTo(x, screenHeight).stroke({ width: 1, color: 0x00f0ff, alpha: 0.055 });
      }
      for (let y = 0; y <= screenHeight; y += gridStep) {
        this.gridGfx.moveTo(0, y).lineTo(screenWidth, y).stroke({ width: 1, color: 0x00f0ff, alpha: 0.055 });
      }
    }

    // 3. Redraw Scanlines (1px dark horizontal lines, 24% opacity, 3px spacing)
    if (this.scanlinesGfx) {
      this.scanlinesGfx.clear();
      for (let y = 0; y <= screenHeight; y += 3) {
        this.scanlinesGfx.moveTo(0, y).lineTo(screenWidth, y).stroke({ width: 1, color: 0x000000, alpha: 0.24 });
      }
    }

    // 4. Position & render Data Trace columns in margins (2 on left, 2 on right)
    if (this.dataColumns && this.dataColumns.length === 4) {
      const colPositions = [18, 38, screenWidth - 40, screenWidth - 20];
      this.dataColumns.forEach((col, idx) => {
        col.x = colPositions[idx];
      });
      this.renderDataTraceColumns(screenHeight);
    }

    // 5. Position avatar container at bottom center
    this.stageContainer.x = screenWidth / 2;
    this.stageContainer.y = screenHeight;

    const texHeight = this.primarySprite.texture ? this.primarySprite.texture.height : 1274;
    // Scale so Kurisu occupies ~94% of the viewport height with 0 aspect ratio distortion
    const targetHeight = screenHeight * 0.94;
    this.baseScale = targetHeight / texHeight;

    this.primarySprite.scale.set(this.baseScale);
    this.secondarySprite.scale.set(this.baseScale);
  }

  notifyState() {
    if (this.onStateChange) {
      this.onStateChange({
        emotion: this.currentEmotion,
        pose: this.currentPose,
        mouth: this.currentMouth,
        isBlinking: this.isBlinking,
      });
    }
  }
}
