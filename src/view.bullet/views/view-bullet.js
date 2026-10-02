import { runtime, unwrap } from "/core/runtime.js"
import { ViewCanvasBase } from "/util/view-canvas-base.js"

const VIEW_WIDTH = 480
const VIEW_HEIGHT = 640
const ROOT_X = VIEW_WIDTH / 2
const ROOT_Y = 72
const DEFAULT_TARGET_X = VIEW_WIDTH / 2
const DEFAULT_TARGET_Y = VIEW_HEIGHT - 72
const MAX_INSTRUCTIONS_PER_TICK = 2048
const MAX_BULLETS = 4096

function assert(condition, message) {
    if (!condition) throw new Error(message)
}

function basename(path) {
    const parts = String(path).split("/")
    return parts[parts.length - 1] || "new.bulletml.json"
}

function errorMessage(error) {
    if (error instanceof Error) return error.message
    return String(error)
}

function requireObject(value, name) {
    assert(value && typeof value === "object" && !Array.isArray(value), `${name} must be an object`)
    return value
}

function requireArray(value, name) {
    assert(Array.isArray(value), `${name} must be an array`)
    return value
}

function createEmptyBulletml() {
    return {
        type: "vertical",
        bullets: [],
        actions: [[]],
        fires: [],
    }
}

function stringifyBulletml(bulletml) {
    return `${JSON.stringify(bulletml, null, 2)}\n`
}

function requireCommand(command) {
    requireObject(command, "JSON BulletML command")
    const keys = Object.keys(command)
    assert(keys.length === 1, "JSON BulletML command must contain exactly one command property")
    return { name: keys[0], value: command[keys[0]] }
}

function requireIndex(value, name) {
    assert(Number.isInteger(value) && value >= 0, `${name} must be a non-negative integer`)
    return value
}

function degToRad(degrees) {
    return (degrees * Math.PI) / 180
}

function velocityFromDirection(direction, speed) {
    const radians = degToRad(direction)
    return {
        x: Math.sin(radians) * speed,
        y: -Math.cos(radians) * speed,
    }
}

function directionToPoint(fromX, fromY, toX, toY) {
    return (Math.atan2(toX - fromX, -(toY - fromY)) * 180) / Math.PI
}

function normalizeDirection(direction) {
    const normalized = direction % 360
    return normalized < 0 ? normalized + 360 : normalized
}

function createFrame(action, params = [], repeat = 1) {
    assert(Array.isArray(action), "JSON BulletML action frame requires command array")
    return { action, pc: 0, params, repeat }
}

class BulletMLEngine {
    constructor() {
        this.bulletml = null
        this.actions = []
        this.bullets = []
        this.fires = []
        this.entities = []
        this.frame = 0
        this.spawned = 0
        this.vanished = 0
        this.rank = 0.5
        this.seed = 1
        this.targetX = DEFAULT_TARGET_X
        this.targetY = DEFAULT_TARGET_Y
        this.running = false
        this.lastInstructionCount = 0
    }

    load(bulletml) {
        this.validateDocument(bulletml)
        this.bulletml = bulletml
        this.actions = bulletml.actions
        this.bullets = bulletml.bullets
        this.fires = bulletml.fires
        this.reset()
    }

    validateDocument(bulletml) {
        requireObject(bulletml, "JSON BulletML document")
        assert(["none", "vertical", "horizontal"].includes(bulletml.type), "JSON BulletML type must be none, vertical, or horizontal")
        requireArray(bulletml.bullets, "JSON BulletML bullets")
        requireArray(bulletml.actions, "JSON BulletML actions")
        requireArray(bulletml.fires, "JSON BulletML fires")
        assert(bulletml.actions.length > 0, "JSON BulletML requires at least one action")
        for (const [index, action] of bulletml.actions.entries()) {
            requireArray(action, `JSON BulletML action ${index}`)
        }
        for (const [index, bullet] of bulletml.bullets.entries()) {
            requireObject(bullet, `JSON BulletML bullet ${index}`)
        }
        for (const [index, fire] of bulletml.fires.entries()) {
            requireObject(fire, `JSON BulletML fire ${index}`)
            assert(Object.hasOwn(fire, "bulletRef"), `JSON BulletML fire ${index} requires bulletRef`)
        }
    }

    reset() {
        this.entities = []
        this.frame = 0
        this.spawned = 0
        this.vanished = 0
        this.seed = 1
        this.lastInstructionCount = 0
        if (!this.bulletml) return
        this.startRootActions()
    }

    startRootActions() {
        assert(this.bulletml !== null, "JSON BulletML root start requires loaded document")
        const roots = this.rootControllers()
        if (roots.length > 0) {
            for (const root of roots) {
                if (this.isScriptActive(root)) continue
                this.restartRootEntity(root)
            }
            return
        }
        const action = this.resolveAction(0)
        const wrapperRefs = this.rootWrapperRefs(action)
        if (wrapperRefs.length > 0) {
            for (const [index, ref] of wrapperRefs.entries()) {
                this.entities.push(this.createRootEntity(index, this.resolveAction(ref.index), ref.params))
            }
            return
        }
        this.entities.push(this.createRootEntity(0, action, []))
    }

    rootControllers() {
        return this.entities.filter((entity) => entity.isRoot)
    }

    restartRootEntity(entity) {
        entity.alive = true
        entity.wait = 0
        entity.frames = [createFrame(entity.rootAction, entity.rootParams)]
    }

    rootWrapperRefs(action) {
        if (action.length <= 1) return []
        const refs = []
        for (const command of action) {
            const { name, value } = requireCommand(command)
            if (name !== "actionRef") return []
            refs.push(this.evalRef(value, [], "root.actionRef"))
        }
        return refs
    }

    createRootEntity(id, action, params) {
        return {
            id,
            visible: false,
            x: ROOT_X,
            y: ROOT_Y,
            direction: 180,
            speed: 0,
            accelX: 0,
            accelY: 0,
            wait: 0,
            alive: true,
            isRoot: true,
            rootAction: action,
            rootParams: params,
            lastFireDirection: 180,
            lastFireSpeed: 1,
            frames: [createFrame(action, params)],
            dirTween: null,
            speedTween: null,
            accelTween: null,
        }
    }

    step() {
        if (!this.bulletml) return
        this.frame += 1
        this.lastInstructionCount = 0
        const entities = [...this.entities]
        for (const entity of entities) {
            if (!entity.alive) continue
            this.updateTweens(entity)
            this.moveEntity(entity)
            this.runEntity(entity)
            if (entity.visible && this.isOutOfBounds(entity)) this.kill(entity)
        }
        this.entities = this.entities.filter((entity) => entity.alive)
    }

    updateTweens(entity) {
        this.updateTween(entity, "direction", "dirTween")
        this.updateTween(entity, "speed", "speedTween")
        if (entity.accelTween) {
            const tween = entity.accelTween
            if (tween.remaining <= 0) {
                entity.accelX = tween.targetX
                entity.accelY = tween.targetY
                entity.accelTween = null
            } else {
                entity.accelX += (tween.targetX - entity.accelX) / tween.remaining
                entity.accelY += (tween.targetY - entity.accelY) / tween.remaining
                tween.remaining -= 1
            }
        }
    }

    updateTween(entity, field, tweenField) {
        const tween = entity[tweenField]
        if (!tween) return
        if (tween.remaining <= 0) {
            entity[field] = tween.target
            entity[tweenField] = null
            return
        }
        entity[field] += (tween.target - entity[field]) / tween.remaining
        tween.remaining -= 1
    }

    moveEntity(entity) {
        if (!entity.visible) return
        entity.x += entity.accelX
        entity.y += entity.accelY
        const velocity = velocityFromDirection(entity.direction, entity.speed)
        entity.x += velocity.x
        entity.y += velocity.y
    }

    runEntity(entity) {
        if (entity.wait > 0) {
            entity.wait -= 1
            return
        }

        while (entity.alive && entity.frames.length > 0) {
            this.lastInstructionCount += 1
            assert(
                this.lastInstructionCount <= MAX_INSTRUCTIONS_PER_TICK,
                "JSON BulletML runner exceeded max instructions in one frame",
            )
            const frame = entity.frames[entity.frames.length - 1]
            if (frame.pc >= frame.action.length) {
                if (frame.repeat > 1) {
                    frame.repeat -= 1
                    frame.pc = 0
                    continue
                }
                entity.frames.pop()
                continue
            }
            const command = frame.action[frame.pc]
            frame.pc += 1
            const paused = this.executeCommand(entity, frame, command)
            if (paused) return
        }
    }

    executeCommand(entity, frame, command) {
        const { name, value } = requireCommand(command)
        if (name === "wait") {
            entity.wait = Math.max(0, Math.floor(this.evalValue(value, frame.params)))
            return true
        }
        if (name === "vanish") {
            assert(value === true, "JSON BulletML vanish command value must be true")
            this.kill(entity)
            return true
        }
        if (name === "fireRef") {
            const ref = this.evalRef(value, frame.params, "fireRef")
            this.fire(entity, this.resolveFire(ref.index), ref.params)
            return false
        }
        if (name === "actionRef") {
            const ref = this.evalRef(value, frame.params, "actionRef")
            entity.frames.push(createFrame(this.resolveAction(ref.index), ref.params))
            return false
        }
        if (name === "repeat") {
            const repeatSpec = requireObject(value, "JSON BulletML repeat")
            const repeat = Math.max(0, Math.floor(this.evalValue(repeatSpec.times, frame.params)))
            if (repeat <= 0) return false
            const ref = this.evalRef(repeatSpec.actionRef, frame.params, "repeat.actionRef")
            entity.frames.push(createFrame(this.resolveAction(ref.index), ref.params, repeat))
            return false
        }
        if (name === "changeDirection") {
            const change = requireObject(value, "JSON BulletML changeDirection")
            const target = this.resolveDirection(entity, change.direction, frame.params)
            const term = Math.max(0, Math.floor(this.evalValue(change.term, frame.params)))
            entity.dirTween = { target, remaining: term }
            return false
        }
        if (name === "changeSpeed") {
            const change = requireObject(value, "JSON BulletML changeSpeed")
            const target = this.resolveSpeed(entity, change.speed, frame.params)
            const term = Math.max(0, Math.floor(this.evalValue(change.term, frame.params)))
            entity.speedTween = { target, remaining: term }
            return false
        }
        if (name === "accel") {
            const accel = requireObject(value, "JSON BulletML accel")
            const targetX = Object.hasOwn(accel, "horizontal")
                ? this.resolveAxis(entity.accelX, accel.horizontal, frame.params)
                : entity.accelX
            const targetY = Object.hasOwn(accel, "vertical")
                ? this.resolveAxis(entity.accelY, accel.vertical, frame.params)
                : entity.accelY
            const term = Math.max(0, Math.floor(this.evalValue(accel.term, frame.params)))
            entity.accelTween = { targetX, targetY, remaining: term }
            return false
        }
        throw new Error(`unsupported JSON BulletML command ${name}`)
    }

    fire(parent, fireSpec, params) {
        assert(this.entities.length < MAX_BULLETS, "JSON BulletML runner exceeded max bullets")
        requireObject(fireSpec, "JSON BulletML fire")
        assert(Object.hasOwn(fireSpec, "bulletRef"), "JSON BulletML fire requires bulletRef")
        const direction = this.resolveDirection(parent, fireSpec.direction, params)
        const speed = this.resolveSpeed(parent, fireSpec.speed, params)
        const bullet = this.resolveBulletRef(fireSpec.bulletRef, params)
        const child = {
            id: this.spawned + 1,
            visible: true,
            x: parent.x,
            y: parent.y,
            direction,
            speed,
            accelX: 0,
            accelY: 0,
            wait: 0,
            alive: true,
            lastFireDirection: direction,
            lastFireSpeed: speed,
            frames: this.createBulletFrames(bullet.definition, bullet.params),
            dirTween: null,
            speedTween: null,
            accelTween: null,
        }
        this.applyBulletInitializers(parent, child, bullet.definition, bullet.params)
        parent.lastFireDirection = direction
        parent.lastFireSpeed = speed
        this.spawned += 1
        this.entities.push(child)
    }

    createBulletFrames(bullet, params) {
        const frames = []
        const actionRefs = bullet.actionRefs === undefined ? [] : requireArray(bullet.actionRefs, "JSON BulletML bullet actionRefs")
        for (const actionRef of actionRefs) {
            const ref = this.evalRef(actionRef, params, "bullet.actionRefs[]")
            frames.push(createFrame(this.resolveAction(ref.index), ref.params))
        }
        return frames
    }

    applyBulletInitializers(parent, child, bullet, params) {
        if (bullet.direction) child.direction = this.resolveDirection(parent, bullet.direction, params)
        if (bullet.speed) child.speed = this.resolveSpeed(parent, bullet.speed, params)
    }

    resolveBulletRef(bulletRef, params) {
        const ref = this.evalRef(bulletRef, params, "bulletRef")
        return { definition: this.resolveBullet(ref.index), params: ref.params }
    }

    resolveDirection(entity, valueSpec, params) {
        if (valueSpec === undefined) return directionToPoint(entity.x, entity.y, this.targetX, this.targetY)
        const spec = requireObject(valueSpec, "JSON BulletML direction")
        const value = this.evalValue(spec.value, params)
        if (spec.type === "aim") return normalizeDirection(directionToPoint(entity.x, entity.y, this.targetX, this.targetY) + value)
        if (spec.type === "absolute") return normalizeDirection(value)
        if (spec.type === "relative") return normalizeDirection(entity.direction + value)
        if (spec.type === "sequence") return normalizeDirection(entity.lastFireDirection + value)
        throw new Error(`unsupported direction type ${spec.type}`)
    }

    resolveSpeed(entity, valueSpec, params) {
        if (valueSpec === undefined) return 1
        const spec = requireObject(valueSpec, "JSON BulletML speed")
        const value = this.evalValue(spec.value, params)
        if (spec.type === "absolute") return value
        if (spec.type === "relative") return entity.speed + value
        if (spec.type === "sequence") return entity.lastFireSpeed + value
        throw new Error(`unsupported speed type ${spec.type}`)
    }

    resolveAxis(current, valueSpec, params) {
        const spec = requireObject(valueSpec, "JSON BulletML acceleration")
        const value = this.evalValue(spec.value, params)
        if (spec.type === "absolute") return value
        if (spec.type === "relative") return current + value
        if (spec.type === "sequence") return current + value
        throw new Error(`unsupported accel type ${spec.type}`)
    }

    evalRef(ref, params, name) {
        if (Number.isInteger(ref)) return { index: requireIndex(ref, name), params }
        const refObject = requireObject(ref, `JSON BulletML ${name}`)
        const index = requireIndex(refObject.ref, `${name}.ref`)
        const refParams = requireArray(refObject.params, `${name}.params`).map((value) => this.evalValue(value, params))
        return { index, params: refParams }
    }

    evalValue(value, params) {
        if (typeof value === "number") {
            assert(Number.isFinite(value), "JSON BulletML numeric value must be finite")
            return value
        }
        assert(typeof value === "string" && value.trim().length > 0, "JSON BulletML expression value must be non-empty string")
        const source = value.trim()
        const rewritten = source.replace(/\$(\d+|rand|rank)/g, (_match, name) => {
            if (name === "rand") return `(${this.random()})`
            if (name === "rank") return `(${this.rank})`
            const index = Number(name) - 1
            assert(index >= 0 && index < params.length, `JSON BulletML expression missing parameter $${name}`)
            return `(${params[index]})`
        })
        assert(/^[0-9+\-*/%().\s]+$/.test(rewritten), `JSON BulletML expression contains unsupported syntax: ${source}`)
        const result = Function(`"use strict"; return (${rewritten})`)()
        assert(Number.isFinite(result), "JSON BulletML expression did not produce finite number")
        return result
    }

    random() {
        this.seed = (1664525 * this.seed + 1013904223) >>> 0
        return this.seed / 0x100000000
    }

    resolveAction(index) {
        requireIndex(index, "action index")
        const action = this.actions[index]
        assert(action, `JSON BulletML missing action ${index}`)
        return action
    }

    resolveBullet(index) {
        requireIndex(index, "bullet index")
        const bullet = this.bullets[index]
        assert(bullet, `JSON BulletML missing bullet ${index}`)
        return bullet
    }

    resolveFire(index) {
        requireIndex(index, "fire index")
        const fire = this.fires[index]
        assert(fire, `JSON BulletML missing fire ${index}`)
        return fire
    }

    kill(entity) {
        if (!entity.alive) return
        entity.alive = false
        this.vanished += 1
    }

    isOutOfBounds(entity) {
        return entity.x < -96 || entity.x > VIEW_WIDTH + 96 || entity.y < -96 || entity.y > VIEW_HEIGHT + 96
    }

    visibleBullets() {
        return this.entities.filter((entity) => entity.alive && entity.visible)
    }

    isScriptActive(entity) {
        return entity.alive && (entity.wait > 0 || entity.frames.length > 0)
    }

    isSpawningDone() {
        return !this.entities.some((entity) => this.isScriptActive(entity))
    }

    areRootActionsDone() {
        return this.rootControllers().every((entity) => !this.isScriptActive(entity))
    }

    isSimulationDone() {
        return this.isSpawningDone() && this.visibleBullets().length === 0
    }

    phase() {
        if (this.isSimulationDone()) return "finished"
        if (this.isSpawningDone()) return "draining"
        return "running"
    }

    stats() {
        return {
            frame: this.frame,
            alive: this.visibleBullets().length,
            spawned: this.spawned,
            vanished: this.vanished,
            instructions: this.lastInstructionCount,
            phase: this.phase(),
        }
    }
}

export class ViewBullet extends ViewCanvasBase {
    static get observedAttributes() {
        return ["data-source"]
    }

    constructor() {
        super()
        this.sourcePath = ""
        this.bulletml = null
        this.programText = ""
        this.dirty = false
        this.engine = new BulletMLEngine()
        this.holdFire = false
        this.pathOutput = null
        this.dirtyOutput = null
        this.statusOutput = null
        this.statsOutput = null
        this._animationFrame = 0
        this._animationToken = 0
        this._lastAnimationTime = 0
        this._ready = false
        this._headerControlsBound = false
        this._lastPlayPausePointerDown = -Infinity
        this._lastHoldFirePointerDown = -Infinity
        this._draggingTarget = false
        this._animate = this._animate.bind(this)
    }

    connectedCallback() {
        if (!this._ready) {
            const config = this.config
            const configSource = config && typeof config === "object" ? config.defaultSource : undefined
            const attrSource = this.getAttribute("data-source")
            assert(
                (typeof attrSource === "string" && attrSource.trim().length > 0) ||
                    (typeof configSource === "string" && configSource.trim().length > 0),
                "view-bullet requires data-source or config.defaultSource",
            )
            this.sourcePath = String(attrSource || configSource).trim()
            this.autoFitOnLoad = true
            this.innerHTML = `
        <canvas data-element="canvas"></canvas>
        <footer>
          <output data-element="path"></output>
          <output data-element="dirty"></output>
          <output data-element="stats"></output>
          <output data-element="status">Loading...</output>
        </footer>
      `
            this.pathOutput = this.querySelector('[data-element="path"]')
            this.dirtyOutput = this.querySelector('[data-element="dirty"]')
            this.statusOutput = this.querySelector('[data-element="status"]')
            this.statsOutput = this.querySelector('[data-element="stats"]')
            assert(this.pathOutput instanceof HTMLOutputElement, "view-bullet missing path output")
            assert(this.dirtyOutput instanceof HTMLOutputElement, "view-bullet missing dirty output")
            assert(this.statusOutput instanceof HTMLOutputElement, "view-bullet missing status output")
            assert(this.statsOutput instanceof HTMLOutputElement, "view-bullet missing stats output")
            this._ready = true
        }
        super.connectedCallback()
        this.updateFooter()
        void this.load()
    }

    disconnectedCallback() {
        this.stopPlayback()
        super.disconnectedCallback()
        this._headerControlsBound = false
    }

    attributeChangedCallback(name, oldValue, newValue) {
        if (oldValue === newValue) return
        if (name !== "data-source") return
        this.sourcePath = String(newValue || "").trim()
        if (!this._ready) return
        assert(this.sourcePath.length > 0, "view-bullet data-source must not be empty")
        this.updateFooter()
        void this.load()
    }

    createHeaderControlsElement() {
        const controls = document.createElement("div")
        controls.dataset.element = "header-controls"
        controls.innerHTML = `
      <div role="buttongroup" data-element="file-actions">
        <button type="button" data-action="new" aria-label="New JSON BulletML" title="New JSON BulletML"><i aria-hidden="true">docs</i></button>
        <button type="button" data-action="open" aria-label="Open JSON BulletML" title="Open JSON BulletML"><i aria-hidden="true">folder_open</i></button>
        <button type="button" data-action="save" class="accent" aria-label="Save JSON BulletML" title="Save JSON BulletML"><i aria-hidden="true">save</i></button>
        <button type="button" data-action="save-as" aria-label="Save JSON BulletML as" title="Save JSON BulletML as"><i aria-hidden="true">save_as</i></button>
        <button type="button" data-action="reload" aria-label="Reload JSON BulletML" title="Reload JSON BulletML"><i aria-hidden="true">refresh</i></button>
      </div>
      <div role="buttongroup" data-element="tool-actions">
        <button type="button" data-action="edit" aria-label="Edit JSON BulletML" title="Edit JSON BulletML"><i aria-hidden="true">edit</i></button>
        <button type="button" data-action="restart" aria-label="Restart preview" title="Restart preview"><i aria-hidden="true">restart_alt</i></button>
        <button type="button" data-action="play-pause" aria-label="Play preview" title="Play preview" aria-pressed="false"><i aria-hidden="true">play_arrow</i></button>
        <button type="button" data-action="hold-fire" aria-label="Hold fire" title="Hold fire" aria-pressed="false"><i aria-hidden="true">repeat</i></button>
        <button type="button" data-action="step" aria-label="Step one frame" title="Step one frame"><i aria-hidden="true">skip_next</i></button>
      </div>
      <div role="buttongroup" data-element="view-actions">
        <button type="button" data-action="zoom-in" aria-label="Zoom In" title="Zoom In"><i aria-hidden="true">zoom_in</i></button>
        <button type="button" data-action="zoom-fit" aria-label="Fit View" title="Fit View"><i aria-hidden="true">fit_screen</i></button>
        <button type="button" data-action="zoom-out" aria-label="Zoom Out" title="Zoom Out"><i aria-hidden="true">zoom_out</i></button>
      </div>
    `
        queueMicrotask(() => this.bindHeaderControls())
        return controls
    }

    bindHeaderControls() {
        if (this._headerControlsBound) return
        this._headerControlsBound = true
        this.headerButton("new").addEventListener("click", () => void this.new())
        this.headerButton("open").addEventListener("click", () => void this.open())
        this.headerButton("save").addEventListener("click", () => void this.save())
        this.headerButton("save-as").addEventListener("click", () => void this.saveAs())
        this.headerButton("reload").addEventListener("click", () => void this.reload())
        this.headerButton("edit").addEventListener("click", () => void this.edit())
        this.headerButton("restart").addEventListener("click", (event) => {
            event.preventDefault()
            this.restartPreview()
        })
        const playPauseButton = this.headerButton("play-pause")
        playPauseButton.addEventListener("pointerdown", (event) => {
            if (event.button !== 0) return
            event.preventDefault()
            this._lastPlayPausePointerDown = performance.now()
            this.togglePlayback()
        })
        playPauseButton.addEventListener("click", (event) => {
            event.preventDefault()
            if (event.detail > 0 && performance.now() - this._lastPlayPausePointerDown < 1000) return
            this.togglePlayback()
        })
        const holdFireButton = this.headerButton("hold-fire")
        holdFireButton.addEventListener("pointerdown", (event) => {
            if (event.button !== 0) return
            event.preventDefault()
            this._lastHoldFirePointerDown = performance.now()
            this.toggleHoldFire()
        })
        holdFireButton.addEventListener("click", (event) => {
            event.preventDefault()
            if (event.detail > 0 && performance.now() - this._lastHoldFirePointerDown < 1000) return
            this.toggleHoldFire()
        })
        this.headerButton("step").addEventListener("click", (event) => {
            event.preventDefault()
            this.stepPreview()
        })
        this.headerButton("zoom-in").addEventListener("click", () => this.zoomIn())
        this.headerButton("zoom-fit").addEventListener("click", () => this.zoomFit())
        this.headerButton("zoom-out").addEventListener("click", () => this.zoomOut())
        this.renderHeaderControls()
    }

    headerButton(action) {
        const element = this.queryHeaderControl(`[data-action="${action}"]`)
        assert(element instanceof HTMLButtonElement, `view-bullet missing ${action} header button`)
        return element
    }

    renderHeaderControls() {
        if (!this._headerControlsElement) return
        const hasProgram = this.bulletml !== null
        const hasPath = this.sourcePath.length > 0
        this.headerButton("save").disabled = !hasProgram || !hasPath
        this.headerButton("save-as").disabled = !hasProgram
        this.headerButton("reload").disabled = !hasPath
        this.headerButton("edit").disabled = !hasPath
        this.headerButton("restart").disabled = !hasProgram
        this.headerButton("play-pause").disabled = !hasProgram
        this.headerButton("hold-fire").disabled = !hasProgram
        this.headerButton("step").disabled = !hasProgram
        const playbackButton = this.headerButton("play-pause")
        const playIcon = playbackButton.querySelector("i")
        assert(playIcon instanceof HTMLElement, "view-bullet play-pause button missing icon")
        const running = this.engine.running
        playIcon.textContent = running ? "pause" : "play_arrow"
        playbackButton.setAttribute("aria-pressed", running ? "true" : "false")
        playbackButton.setAttribute("aria-label", running ? "Pause preview" : "Play preview")
        playbackButton.setAttribute("title", running ? "Pause preview" : "Play preview")
        const holdFireButton = this.headerButton("hold-fire")
        const holdFireIcon = holdFireButton.querySelector("i")
        assert(holdFireIcon instanceof HTMLElement, "view-bullet hold-fire button missing icon")
        holdFireIcon.textContent = "repeat"
        holdFireButton.setAttribute("aria-pressed", this.holdFire ? "true" : "false")
        holdFireButton.setAttribute("aria-label", this.holdFire ? "Disable hold fire" : "Hold fire")
        holdFireButton.setAttribute("title", this.holdFire ? "Disable hold fire" : "Hold fire")
        holdFireButton.classList.toggle("accent", this.holdFire)
    }

    calculateContentBounds(_data) {
        return { minX: 0, minY: 0, maxX: VIEW_WIDTH, maxY: VIEW_HEIGHT }
    }

    drawContent(ctx, _data) {
        const visibleMinX = -this.offsetX / this.scale
        const visibleMinY = -this.offsetY / this.scale
        const visibleWidth = this.canvas.width / this.scale
        const visibleHeight = this.canvas.height / this.scale
        ctx.fillStyle = "#08131a"
        ctx.fillRect(visibleMinX, visibleMinY, visibleWidth, visibleHeight)

        ctx.fillStyle = "#54d6ff"
        ctx.beginPath()
        ctx.arc(ROOT_X, ROOT_Y, 5, 0, Math.PI * 2)
        ctx.fill()

        ctx.strokeStyle = "#6aff88"
        ctx.beginPath()
        ctx.moveTo(this.engine.targetX - 8, this.engine.targetY)
        ctx.lineTo(this.engine.targetX + 8, this.engine.targetY)
        ctx.moveTo(this.engine.targetX, this.engine.targetY - 8)
        ctx.lineTo(this.engine.targetX, this.engine.targetY + 8)
        ctx.stroke()

        for (const bullet of this.engine.visibleBullets()) {
            ctx.fillStyle = this.bulletColor(bullet)
            ctx.beginPath()
            ctx.arc(bullet.x, bullet.y, 3, 0, Math.PI * 2)
            ctx.fill()
            const velocity = velocityFromDirection(bullet.direction, 8)
            ctx.strokeStyle = "#ffd1dc"
            ctx.beginPath()
            ctx.moveTo(bullet.x, bullet.y)
            ctx.lineTo(bullet.x + velocity.x, bullet.y + velocity.y)
            ctx.stroke()
        }
    }

    bulletColor(_bullet) {
        return "#ff6688"
    }

    onCanvasMouseDown(event) {
        this._draggingTarget = true
        this.moveTargetToEvent(event)
    }

    onCanvasMouseMove(event) {
        if (!this._draggingTarget) return
        this.moveTargetToEvent(event)
    }

    onCanvasMouseUp(_event) {
        this._draggingTarget = false
    }

    onCanvasMouseLeave(_event) {
        this._draggingTarget = false
    }

    moveTargetToEvent(event) {
        const point = this.getWorldPoint(event.clientX, event.clientY)
        this.engine.targetX = point.x
        this.engine.targetY = point.y
        this.draw()
    }

    setStatus(text, tone = null) {
        assert(this.statusOutput instanceof HTMLOutputElement, "view-bullet missing status output")
        this.statusOutput.textContent = text
        this.statusOutput.classList.remove("accent", "success", "warning", "danger", "info")
        if (tone) this.statusOutput.classList.add(tone)
    }

    updateFooter(status = null, tone = null) {
        assert(this.pathOutput instanceof HTMLOutputElement, "view-bullet missing path output")
        assert(this.dirtyOutput instanceof HTMLOutputElement, "view-bullet missing dirty output")
        assert(this.statsOutput instanceof HTMLOutputElement, "view-bullet missing stats output")
        this.pathOutput.textContent = `Path: ${this.sourcePath}`
        this.dirtyOutput.textContent = this.dirty ? "Dirty" : "Saved"
        this.dirtyOutput.className = this.dirty ? "warning" : "success"
        const stats = this.engine.stats()
        this.statsOutput.textContent = `Frame: ${stats.frame} Phase: ${stats.phase} Alive: ${stats.alive} Spawned: ${stats.spawned} Vanished: ${stats.vanished}`
        if (status !== null) this.setStatus(status, tone)
        this.renderHeaderControls()
    }

    setProgram(bulletml, { dirty = false, status = "Ready", tone = "success" } = {}) {
        this.bulletml = bulletml
        this.programText = stringifyBulletml(bulletml)
        this.dirty = dirty
        this.engine.load(bulletml)
        this.setData(bulletml, { autoFit: true })
        this.updateFooter(status, tone)
    }

    async load() {
        assert(this.sourcePath.length > 0, "view-bullet load requires source path")
        this.stopPlayback()
        this.setStatus("Loading...", "info")
        try {
            const text = unwrap(await runtime.invoke("fs/fs::read-text", this.sourcePath))
            const bulletml = JSON.parse(text)
            this.setProgram(bulletml, { dirty: false, status: "Ready", tone: "success" })
        } catch (error) {
            this.setStatus(`Error: ${errorMessage(error)}`, "danger")
            console.error("view-bullet load failed:", error)
        }
    }

    async reload() {
        await this.load()
        await runtime.call("ui.toast.success", { message: `Reloaded ${this.sourcePath}` })
    }

    async new() {
        const payload = unwrap(await runtime.call("ui.popup.open", this.createNewPopupOptions()))
        if (!payload || payload.cancelled) return
        const path = typeof payload.path === "string" ? payload.path.trim() : ""
        assert(path.length > 0, "view-bullet new requires JSON BulletML file path")
        this.sourcePath = path
        this.setProgram(createEmptyBulletml(), { dirty: false, status: `Created ${path}`, tone: "success" })
        await this.saveToPath(path)
        await runtime.call("ui.toast.success", { message: `Created ${path}` })
    }

    async open() {
        const payload = unwrap(await runtime.call("ui.popup.open", this.createOpenPopupOptions()))
        if (!payload || payload.cancelled) return
        const selection = Array.isArray(payload.selection) ? payload.selection[0] : payload.selection
        assert(selection && selection.path, "view-bullet open requires selected JSON BulletML file path")
        this.sourcePath = selection.path
        await this.load()
    }

    async save() {
        assert(this.bulletml !== null, "view-bullet save requires loaded JSON BulletML")
        assert(this.sourcePath.length > 0, "view-bullet save requires JSON BulletML file path")
        await this.saveToPath(this.sourcePath)
        this.dirty = false
        this.updateFooter(`Saved ${this.sourcePath}`, "success")
        await runtime.call("ui.toast.success", { message: `Saved ${this.sourcePath}` })
    }

    async saveAs() {
        assert(this.bulletml !== null, "view-bullet save-as requires loaded JSON BulletML")
        const payload = unwrap(await runtime.call("ui.popup.open", this.createSavePopupOptions()))
        if (!payload || payload.cancelled) return
        const path = typeof payload.path === "string" ? payload.path.trim() : ""
        assert(path.length > 0, "view-bullet save-as requires JSON BulletML file path")
        await this.saveToPath(path)
        this.sourcePath = path
        this.dirty = false
        this.updateFooter(`Saved as ${path}`, "success")
        await runtime.call("ui.toast.success", { message: `Saved ${path}` })
    }

    async edit() {
        assert(this.sourcePath.length > 0, "view-bullet edit requires JSON BulletML file path")
        const payload = unwrap(
            await runtime.call("ui.popup.open", {
                title: "Edit JSON BulletML",
                size: "large",
                tag: "view-code",
                attributes: { "data-source": this.sourcePath, "data-lang": "json" },
            }),
        )
        if (payload && payload.reload) await this.load()
    }

    async saveToPath(path) {
        assert(this.bulletml !== null, "view-bullet save requires loaded JSON BulletML")
        assert(typeof path === "string" && path.length > 0, "view-bullet save requires JSON BulletML path")
        this.programText = stringifyBulletml(this.bulletml)
        unwrap(await runtime.invoke("fs/fs::write-text", path, this.programText))
    }

    restartPreview() {
        assert(this.bulletml !== null, "view-bullet restart requires loaded JSON BulletML")
        this.holdFire = false
        this.engine.reset()
        this.draw()
        this.updateFooter("Restarted", "success")
    }

    stepPreview() {
        assert(this.bulletml !== null, "view-bullet step requires loaded JSON BulletML")
        this.engine.step()
        this.draw()
        this.updateFooter("Stepped one frame", "info")
    }

    togglePlayback() {
        assert(this.bulletml !== null, "view-bullet playback requires loaded JSON BulletML")
        if (this.engine.running) this.stopPlayback()
        else this.startPlayback()
        this.renderHeaderControls()
    }

    toggleHoldFire() {
        assert(this.bulletml !== null, "view-bullet hold fire requires loaded JSON BulletML")
        this.holdFire = !this.holdFire
        if (this.holdFire) {
            if (this.engine.areRootActionsDone()) this.engine.startRootActions()
            this.startPlayback({ restartFinished: false })
            this.updateFooter("Repeat on", "info")
            return
        }
        this.updateFooter("Repeat off", "info")
    }

    startPlayback({ restartFinished = true } = {}) {
        if (this.engine.running) return
        if (restartFinished && this.engine.isSimulationDone()) this.engine.reset()
        this.engine.running = true
        this._animationToken += 1
        this._lastAnimationTime = 0
        const token = this._animationToken
        this._animationFrame = requestAnimationFrame((time) => this._animate(time, token))
    }

    stopPlayback() {
        this.engine.running = false
        this._animationToken += 1
        if (this._animationFrame) cancelAnimationFrame(this._animationFrame)
        this._animationFrame = 0
        this.renderHeaderControls()
    }

    _animate(time, token) {
        this._animationFrame = 0
        if (!this.engine.running || token !== this._animationToken) return
        if (this._lastAnimationTime === 0) this._lastAnimationTime = time
        const elapsed = time - this._lastAnimationTime
        const steps = Math.max(1, Math.min(4, Math.floor(elapsed / (1000 / 60)) || 1))
        try {
            for (let i = 0; i < steps; i += 1) this.engine.step()
        } catch (error) {
            this.stopPlayback()
            this.updateFooter(`Error: ${errorMessage(error)}`, "danger")
            console.error("view-bullet playback failed:", error)
            return
        }
        this._lastAnimationTime = time
        if (this.holdFire && this.engine.areRootActionsDone()) this.engine.startRootActions()
        this.draw()
        if (this.engine.isSimulationDone()) {
            this.stopPlayback()
            this.updateFooter("Finished", "success")
            return
        }
        this.updateFooter(this.holdFire ? "Holding fire" : null, this.holdFire ? "info" : null)
        if (!this.engine.running || token !== this._animationToken) return
        this._animationFrame = requestAnimationFrame((nextTime) => this._animate(nextTime, token))
    }

    createOpenPopupOptions() {
        return {
            title: "Open JSON BulletML",
            size: "medium",
            tag: "view-files",
            props: { mode: "chooser", filter: "*.bulletml.json,*.json" },
        }
    }

    createNewPopupOptions() {
        return {
            title: "Create JSON BulletML",
            size: "medium",
            tag: "view-files",
            props: { mode: "saver", filter: "*.bulletml.json,*.json", defaultName: "new.bulletml.json" },
        }
    }

    createSavePopupOptions() {
        return {
            title: "Save JSON BulletML As",
            size: "medium",
            tag: "view-files",
            props: {
                mode: "saver",
                filter: "*.bulletml.json,*.json",
                defaultName: basename(this.sourcePath || "new.bulletml.json"),
            },
        }
    }
}

if (!customElements.get("view-bullet")) {
    customElements.define("view-bullet", ViewBullet)
}
