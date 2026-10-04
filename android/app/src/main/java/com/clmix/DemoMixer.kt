package com.clmix

import android.os.Handler
import android.os.Looper
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * A stand-in for the desktop server that answers entirely from memory -
 * no socket, no desktop app, no console, no network of any kind. The
 * iOS DemoMixer, ported; see it for the reasoning behind the fake
 * console's contents, which are identical here down to the seeded
 * levels so the two platforms demo the same show.
 *
 * Where iOS implements its MixerBackend protocol method by method, this
 * one speaks the wire protocol instead: MixerClient hands it the same
 * JSON it would have put on the socket, and it answers with the same
 * JSON the server would have sent back. That is not a shortcut - it is
 * the more faithful of the two, because every reply then goes through
 * MixerClient.handleMessage and the real parsing, permission and
 * dispatch path rather than around it. Nothing above this file knows
 * the demo exists.
 *
 * It pushes on the same cadences the real RemoteServer does
 * (services/remote_server.py), because screens depend on them: the
 * fader's drag grace and the optimistic mute handling are both driven
 * by pushes arriving, and would behave differently against a backend
 * that went quiet.
 */
object DemoMixer {

    /** What ConnectActivity "connects" to. A host no real server can
     *  have, so there is no way to reach this by typing an address. */
    const val HOST = "demo"

    private val handler = Handler(Looper.getMainLooper())

    // MARK: - The fake console

    private enum class Voice { KICK, SNARE, HATS, TOMS, SUSTAINED, SILENT }

    private class DemoChannel(
        val channel: Int,
        val name: String,
        val bank: String,
        // Whether the strip's meter draws two bars rather than one.
        val stereo: Boolean = false,
        // How this input behaves on a meter, so the demo's bars move
        // like a band playing rather than like noise. See amplitude().
        val voice: Voice = Voice.SUSTAINED
    )

    /**
     * A plausible small-band input list, in console order. `bank`
     * mirrors how the desktop app groups channels into banks the phone
     * can filter by; "All" is not a bank here, it is the absence of a
     * filter. Two are stereo pairs, so the two-bar meter is exercised
     * rather than only ever drawn as a single wide bar.
     */
    private val catalog = listOf(
        DemoChannel(1, "Kick In", "Drums", voice = Voice.KICK),
        DemoChannel(2, "Kick Out", "Drums", voice = Voice.KICK),
        DemoChannel(3, "Snare Top", "Drums", voice = Voice.SNARE),
        DemoChannel(4, "Snare Bot", "Drums", voice = Voice.SNARE),
        DemoChannel(5, "Hi-Hat", "Drums", voice = Voice.HATS),
        DemoChannel(6, "Rack Tom", "Drums", voice = Voice.TOMS),
        DemoChannel(7, "Floor Tom", "Drums", voice = Voice.TOMS),
        DemoChannel(8, "OH L", "Drums", voice = Voice.HATS),
        DemoChannel(9, "OH R", "Drums", voice = Voice.HATS),
        DemoChannel(10, "Bass DI", "Band"),
        DemoChannel(11, "Bass Amp", "Band"),
        DemoChannel(12, "Gtr Stage L", "Band"),
        DemoChannel(13, "Gtr Stage R", "Band"),
        DemoChannel(14, "Keys", "Band", stereo = true),
        DemoChannel(15, "Playback", "Band", stereo = true),
        DemoChannel(16, "Acoustic", "Band"),
        DemoChannel(17, "Lead Vox", "Vocals"),
        DemoChannel(18, "BV Stage L", "Vocals"),
        DemoChannel(19, "BV Stage R", "Vocals"),
        DemoChannel(20, "Talkback", "Vocals", voice = Voice.SILENT)
    )

    /** "Drum Sub" is deliberately mono - a real rig usually has one, and
     *  it is what makes the Pan button's disappearance on a bus with no
     *  pan axis visible in the demo rather than only against a console. */
    private val auxes = listOf(
        AuxBus(1, "Wedge 1 - Lead Vox"),
        AuxBus(2, "Wedge 2 - Guitar"),
        AuxBus(3, "Wedge 3 - Bass"),
        AuxBus(4, "Wedge 4 - Keys"),
        AuxBus(5, "IEM - Drums"),
        AuxBus(6, "IEM - MD"),
        AuxBus(7, "Side Fills"),
        AuxBus(8, "Drum Sub", stereo = false)
    )

    private val banks = listOf("Drums", "Band", "Vocals")

    /** What each wedge's owner most wants to hear, seeded loudest in
     *  their own mix - so the demo opens on something shaped like a real
     *  monitor mix instead of a wall of identical faders. */
    private val featuredChannel = mapOf(
        1 to 17, 2 to 12, 3 to 10, 4 to 14, 5 to 1, 6 to 16, 7 to 17, 8 to 3
    )

    private class Send(var level: Double, var pan: Double, var muted: Boolean)

    /** The console's own channel, as Full Mixer Control rides it - the
     *  channel fader and panner rather than one bus's send, plus the
     *  input stage behind them. */
    private class Strip(
        var name: String,
        var level: Double,
        var pan: Double?,
        var muted: Boolean,
        var gain: Double?,
        var trim: Double?,
        var phantom: Boolean,
        // The console's own enum, 0..3 - see ChannelState.phase.
        var phase: Int
    )

    // [aux index: [channel number: send]]
    private val sends = HashMap<Int, HashMap<Int, Send>>()
    private val presets = HashMap<String, Map<Int, Send>>()
    // [channel number: the console's own strip]
    private val console = HashMap<Int, Strip>()

    // Personal labels, as the server's user record holds them - and like
    // that record, never written back into the catalog: the demo console
    // keeps its own names, and only this account's view of them changes.
    private val personalNames = HashMap<Int, String>()

    // This account's own banks, once it has any. Null until seeded from
    // the catalog below, exactly as the real server seeds from whatever
    // the console reports - see RemoteServer._stored_banks.
    private var customBanks: MutableList<CustomBank>? = null

    private var selectedAux: Int? = null
    // True while riding the console's own faders. Mutually exclusive
    // with selectedAux, the same way the real server's two modes are.
    private var mixerMode = false
    private var selectedBank: String? = null
    private var pushing = false

    /** The one snapshot this fake console knows about. The real server
     *  files personal labels per snapshot because an account can span
     *  several shows; there is only one show here, but the name is still
     *  reported so the rename dialog reads exactly as it does for real. */
    private const val SNAPSHOT = "Demo Show"

    // MARK: - Lifecycle

    fun start() {
        seed()
        selectedAux = null
        mixerMode = false
        selectedBank = null
        personalNames.clear()
    }

    fun stop() {
        stopPushing()
        selectedAux = null
        mixerMode = false
        selectedBank = null
        personalNames.clear()
        customBanks = null
    }

    /** The account's own banks, seeded on first use from the grouping
     *  the fake console reports - the demo account holds personalization,
     *  so it has a set like any other such account would. */
    private fun storedBanks(): MutableList<CustomBank> {
        customBanks?.let { return it }

        val seeded = banks.map { bank ->
            CustomBank(bank, catalog.filter { it.bank == bank }.map { it.channel })
        }.toMutableList()

        customBanks = seeded
        return seeded
    }

    /**
     * Deterministic starting positions, so the demo opens on something
     * that reads as a real monitor mix rather than a wall of identical
     * faders - each performer's own instrument sits loudest in their own
     * wedge, and the talkback starts muted the way it usually would.
     */
    private fun seed() {
        sends.clear()

        for (aux in auxes) {
            val mix = HashMap<Int, Send>()

            for (entry in catalog) {
                // Spread the rest of the band across a believable range,
                // varying per aux so switching aux visibly changes the mix.
                val spread = ((entry.channel * 7 + aux.index * 13) % 24).toDouble()
                var level = -6.0 - spread

                // The vocal is up in everybody's mix, but whatever this
                // particular wedge is for sits above even that.
                if (entry.name == "Lead Vox") level = -4.0
                if (entry.channel == featuredChannel[aux.index]) level = -2.0

                mix[entry.channel] = Send(
                    level = level,
                    pan = panForStagePicture(entry.name),
                    muted = entry.name == "Talkback"
                )
            }

            sends[aux.index] = mix
        }

        // The console's own strips, behind every aux send above. Seeded
        // so the input stage has something to show the moment it is
        // opened: a head amp set where that source would really sit, a
        // trim near unity, and 48V only on what needs it. A couple of
        // channels deliberately report no head amp at all, which is what
        // a console that has not answered for them yet looks like - the
        // dials grey out and read a dash rather than 0 dB.
        console.clear()

        for (entry in catalog) {
            val needsPhantom = entry.name.startsWith("OH") || entry.name.startsWith("BV") ||
                entry.name == "Acoustic" || entry.name == "Talkback"
            val reported = entry.channel % 9 != 0

            console[entry.channel] = Strip(
                name = entry.name,
                level = ((entry.channel * 5) % 11).toDouble() * -1.2 - 1.0,
                // A mono channel has no pan axis on the main mix, exactly
                // as a mono aux has none for its sends.
                pan = if (entry.stereo) 0.0 else panForStagePicture(entry.name),
                muted = entry.name == "Talkback",
                gain = if (reported) (18 + (entry.channel * 7) % 34).toDouble() else null,
                trim = if (reported) ((entry.channel * 3) % 9).toDouble() - 4 else null,
                phantom = needsPhantom,
                phase = phaseForStagePicture(entry.name)
            )
        }

        // Two presets to load straight away, so the Load sheet is not
        // empty the first time it is opened.
        presets.clear()
        presets["Opening Set"] = copyOf(sends[1])
        presets["Acoustic Set"] = copyOf(sends[4])
    }

    private fun copyOf(mix: Map<Int, Send>?): Map<Int, Send> =
        mix?.mapValues { Send(it.value.level, it.value.pan, it.value.muted) } ?: emptyMap()

    /** Anything named L/R sits off-centre, everything else stays up the
     *  middle - the same stage picture the aux sends are seeded with. */
    private fun panForStagePicture(name: String): Double = when {
        name.endsWith(" L") -> -0.4
        name.endsWith(" R") -> 0.4
        else -> 0.0
    }

    /**
     * Polarity, as the console's own enum rather than a flag. "Snare
     * Bot" is the mono channel an engineer would really flip. "Keys" is
     * a stereo pair and is given state 2 so the demo carries at least
     * one value a boolean could not have represented - tapping polarity
     * off and on there has to come back to 2, which is the bug that enum
     * exists to prevent.
     */
    private fun phaseForStagePicture(name: String): Int = when (name) {
        "Snare Bot" -> PHASE_INVERTED
        "Keys" -> 2
        else -> PHASE_NORMAL
    }

    // MARK: - The protocol

    /** Everything MixerClient would have put on the socket arrives
     *  here instead. Unknown actions are ignored, exactly as a server
     *  that does not implement one would. */
    fun handle(message: JSONObject) {
        when (message.optString("action")) {
            "login" -> login()
            "logout" -> stop()
            "list_auxes" -> deliver(auxesMessage())
            "list_banks" -> deliver(banksMessage())

            "select_aux" -> {
                selectedAux = message.optInt("aux")
                mixerMode = false
                // The bank filter deliberately survives an aux change,
                // matching RemoteServer._handle: select_aux sets the aux
                // and leaves the bank alone, and the phone's own bank
                // picker keeps its selection across the switch too.
                startPushing()
            }

            "select_mixer" -> {
                selectedAux = null
                mixerMode = true
                startPushing()
            }

            "select_bank" -> {
                selectedBank = if (message.isNull("bank")) null else message.optString("bank")
                pushLevels()
            }

            "set_level" -> {
                val channel = message.optInt("channel")
                val level = message.optDouble("level")
                if (mixerMode) {
                    console[channel]?.level = level
                } else {
                    currentMix()?.get(channel)?.level = level
                }
            }

            "set_pan" -> {
                val channel = message.optInt("channel")
                val pan = message.optDouble("pan")
                if (mixerMode) {
                    // A channel the console reports no pan axis for
                    // stays without one, as against real hardware.
                    console[channel]?.let { if (it.pan != null) it.pan = pan }
                } else {
                    currentMix()?.get(channel)?.pan = pan
                }
            }

            "set_mute" -> {
                val channel = message.optInt("channel")
                val muted = message.optBoolean("muted")
                if (mixerMode) {
                    console[channel]?.muted = muted

                    // A hard mute is assembled rather than being one
                    // control: the channel mute plus every aux send
                    // dropped, so the channel leaves the monitors as
                    // well as the room.
                    if (message.optBoolean("hard")) {
                        for (mix in sends.values) mix[channel]?.muted = muted
                    }
                } else {
                    currentMix()?.get(channel)?.muted = muted
                }
            }

            // The account's own label for a strip - aux mode only,
            // exactly as the real server has it: mixer mode shows the
            // desk's own names, and set_name renames for real there.
            "set_personal_name" -> {
                if (mixerMode) return
                val channel = message.optInt("channel")
                val name = message.optString("name")
                // An empty name clears the label, the same way the
                // server reads it, so Reset in the rename dialog works.
                if (name.isEmpty()) personalNames.remove(channel) else personalNames[channel] = name
            }

            // The channel's input stage. Mixer mode only, exactly as the
            // real server has it - a head amp feeds every mix and the
            // recording at once, so it is never reachable from an aux.
            "set_gain" -> if (mixerMode) {
                console[message.optInt("channel")]?.gain = message.optDouble("gain")
            }

            "set_trim" -> if (mixerMode) {
                console[message.optInt("channel")]?.trim = message.optDouble("trim")
            }

            "set_phantom" -> if (mixerMode) {
                console[message.optInt("channel")]?.phantom = message.optBoolean("phantom")
            }

            "set_phase" -> if (mixerMode) {
                console[message.optInt("channel")]?.phase = message.optInt("phase")
            }

            "set_name" -> if (mixerMode) {
                // The same cap the real server applies (MAX_CHANNEL_NAME).
                console[message.optInt("channel")]?.name =
                    message.optString("name").take(32)
            }

            "list_custom_banks" -> deliver(customBanksMessage())

            "save_custom_banks" -> {
                val arr = message.optJSONArray("banks") ?: JSONArray()
                val saved = mutableListOf<CustomBank>()

                for (i in 0 until arr.length()) {
                    val o = arr.optJSONObject(i) ?: continue
                    val name = o.optString("name").trim()
                    if (name.isEmpty()) continue

                    val chArr = o.optJSONArray("channels") ?: JSONArray()
                    val channels = (0 until chArr.length())
                        .map { chArr.optInt(it) }
                        .distinct()

                    saved.add(CustomBank(name.take(32), channels))
                }

                customBanks = saved
                afterBanksChanged()
            }

            "reset_custom_banks" -> {
                customBanks = null
                afterBanksChanged()
            }

            "list_presets" -> deliver(
                JSONObject()
                    .put("type", "presets")
                    .put("presets", JSONArray(presets.keys.sorted()))
            )

            "save_preset" -> savePreset(message.optString("name"))
            "load_preset" -> loadPreset(message.optString("name"))
        }
    }

    /** The set, the picker and the strips all follow a change to the
     *  banks - and a bank the client was sitting on may have just been
     *  renamed out from under it, in which case it falls back to the
     *  whole desk. */
    private fun afterBanksChanged() {
        deliver(customBanksMessage())

        if (selectedBank != null && bankNames().none { it == selectedBank }) {
            selectedBank = null
        }

        deliver(banksMessage())
        pushLevels()
    }

    private fun currentMix(): HashMap<Int, Send>? = selectedAux?.let { sends[it] }

    /**
     * Any credentials are accepted: the demo exists to be walked into,
     * and there is no account behind it to get wrong. It is allowed
     * everything, so Presets, hard mute, Full Mixer Control and personal
     * names are all exercised rather than hidden - "all of the features
     * and functionality" is what Guideline 2.1 asks a demonstration mode
     * to show.
     *
     * No token is handed back, so SessionStore stores nothing and a
     * relaunch returns to the connect screen rather than silently
     * resuming into a demo the user may not have wanted again. No
     * head_amp either, which leaves MixerClient on its built-in ranges -
     * the same ones a real console reports.
     */
    private fun login() {
        deliver(
            JSONObject()
                .put("type", "login_result")
                .put("ok", true)
                .put("presets", true)
                .put("mute", true)
                .put("mixer_control", true)
                .put("personalization", true)
        )
    }

    private fun savePreset(name: String) {
        val mix = currentMix()
        if (mix == null) {
            // Never leave the sheet waiting on a reply that is not
            // coming - the save sheet releases on a failure.
            deliver(errorMessage("No aux selected"))
            return
        }

        presets[name] = copyOf(mix)
        deliver(JSONObject().put("type", "preset_saved").put("name", name))
    }

    private fun loadPreset(name: String) {
        val mix = currentMix()
        val stored = presets[name]
        if (mix == null || stored == null) {
            deliver(errorMessage("Preset not found"))
            return
        }

        // Applied as ordinary level/pan changes, exactly as the desktop
        // app recalls a preset - the next push reflects it like any
        // other move.
        for ((channel, send) in stored) {
            mix[channel]?.level = send.level
            mix[channel]?.pan = send.pan
        }

        pushLevels()
        deliver(JSONObject().put("type", "preset_loaded").put("name", name))
    }

    private fun errorMessage(text: String): JSONObject =
        JSONObject().put("type", "error").put("message", text)

    private fun auxesMessage(): JSONObject {
        val arr = JSONArray()
        for (aux in auxes) {
            arr.put(
                JSONObject()
                    .put("index", aux.index)
                    .put("name", aux.name)
                    .put("stereo", aux.stereo)
            )
        }
        return JSONObject().put("type", "auxes").put("auxes", arr)
    }

    private fun banksMessage(): JSONObject =
        JSONObject().put("type", "banks").put("banks", JSONArray(bankNames()))

    /** Custom names in aux mode, the console's own in mixer mode - the
     *  same split the real server makes. */
    private fun bankNames(): List<String> =
        if (mixerMode) banks else storedBanks().map { it.name }

    private fun customBanksMessage(): JSONObject {
        val arr = JSONArray()

        for (bank in storedBanks()) {
            arr.put(
                JSONObject()
                    .put("name", bank.name)
                    .put("channels", JSONArray(bank.channels))
            )
        }

        val chans = JSONArray()

        for (entry in catalog) {
            chans.put(
                JSONObject()
                    .put("channel", entry.channel)
                    .put("name", personalNames[entry.channel] ?: entry.name)
            )
        }

        return JSONObject()
            .put("type", "custom_banks")
            .put("banks", arr)
            .put("channels", chans)
    }

    // MARK: - Pushing

    private val pushLevelsTick = object : Runnable {
        override fun run() {
            pushLevels()
            handler.postDelayed(this, LEVEL_PUSH_MS)
        }
    }

    private val pushMetersTick = object : Runnable {
        override fun run() {
            pushMeters()
            handler.postDelayed(this, METER_PUSH_MS)
        }
    }

    private fun startPushing() {
        stopPushing()
        pushing = true
        pushLevels()
        handler.postDelayed(pushLevelsTick, LEVEL_PUSH_MS)
        handler.postDelayed(pushMetersTick, METER_PUSH_MS)
    }

    private fun stopPushing() {
        pushing = false
        handler.removeCallbacks(pushLevelsTick)
        handler.removeCallbacks(pushMetersTick)
    }

    private fun visibleChannels(): List<DemoChannel> {
        val bank = selectedBank ?: return catalog

        if (!mixerMode) {
            val custom = storedBanks().firstOrNull { it.name == bank }
                ?: return emptyList()
            return catalog.filter { custom.channels.contains(it.channel) }
        }

        return catalog.filter { it.bank == bank }
    }

    private fun pushLevels() {
        val channels = JSONArray()

        if (mixerMode) {
            // The same row shape, read off the channel itself: the
            // console's own fader, panner and mute, plus the input stage
            // behind them. Carries the sentinel aux the real server
            // sends in mixer mode, since no bus is being ridden.
            for (entry in visibleChannels()) {
                val strip = console[entry.channel] ?: continue
                channels.put(
                    JSONObject()
                        .put("channel", entry.channel)
                        .put("name", strip.name)
                        .put("level", strip.level)
                        .put("pan", strip.pan ?: JSONObject.NULL)
                        .put("muted", strip.muted)
                        .put("stereo", entry.stereo)
                        .put("gain", strip.gain ?: JSONObject.NULL)
                        .put("trim", strip.trim ?: JSONObject.NULL)
                        .put("phantom", strip.phantom)
                        .put("phase_state", strip.phase)
                )
            }
            deliver(levelsMessage(MixerClient.MIXER_AUX, channels))
            return
        }

        val aux = selectedAux ?: return
        val mix = sends[aux] ?: return

        for (entry in visibleChannels()) {
            val send = mix[entry.channel] ?: continue
            channels.put(
                JSONObject()
                    .put("channel", entry.channel)
                    // The account's own label wins over the console's
                    // name, exactly as RemoteServer._channel_states
                    // applies it.
                    .put("name", personalNames[entry.channel] ?: entry.name)
                    .put("level", send.level)
                    .put("pan", send.pan)
                    .put("muted", send.muted)
                    .put("stereo", entry.stereo)
            )
        }

        deliver(levelsMessage(aux, channels))
    }

    private fun levelsMessage(aux: Int, channels: JSONArray): JSONObject =
        JSONObject()
            .put("type", "levels")
            .put("aux", aux)
            .put("snapshot", SNAPSHOT)
            .put("channels", channels)

    // MARK: - Fabricated meters

    /**
     * Bars that move like a band playing rather than like noise: each
     * input is given a voice driven off a shared 120bpm clock, so the
     * kick pulses on the beat, the snare answers on 2 and 4, and the
     * sustained sources breathe underneath. Entirely deterministic - a
     * function of the wall clock and the channel number, with no state
     * kept between frames.
     */
    private fun pushMeters() {
        // Post-fader, so the bars follow whichever fader is being
        // ridden: the send in aux mode, the channel's own in mixer mode.
        val mix = if (mixerMode) null else (selectedAux?.let { sends[it] } ?: return)

        val now = System.nanoTime() / 1_000_000_000.0
        val meters = JSONArray()

        for (entry in visibleChannels()) {
            val level: Double
            val muted: Boolean

            if (mixerMode) {
                val strip = console[entry.channel] ?: continue
                level = strip.level
                muted = strip.muted
            } else {
                val send = mix?.get(entry.channel) ?: continue
                level = send.level
                muted = send.muted
            }

            val left = meterDb(entry, level, muted, now, leg = 0)
            // A stereo pair's two legs never sit at exactly the same
            // level; a mono channel reports nothing at all on the right,
            // the same way the console's no-signal sentinel comes
            // through as null.
            val right = if (entry.stereo) meterDb(entry, level, muted, now, leg = 1) else null

            val row = JSONArray()
            row.put(entry.channel)
            row.put(left ?: JSONObject.NULL)
            row.put(left?.minus(4) ?: JSONObject.NULL)
            row.put(right ?: JSONObject.NULL)
            row.put(right?.minus(4) ?: JSONObject.NULL)
            meters.put(row)
        }

        deliver(JSONObject().put("type", "meters").put("meters", meters))
    }

    private fun meterDb(
        entry: DemoChannel,
        level: Double,
        muted: Boolean,
        t: Double,
        leg: Int
    ): Double? {
        // A muted send is not in this mix, so its post-fader meter reads
        // nothing - the same as the console's no-signal sentinel.
        if (muted) return null

        val offset = entry.channel * 0.37 + leg * 0.11
        val amplitude = amplitude(entry.voice, t, offset)
        if (amplitude <= 0) return null

        val base = METER_FLOOR_DB + amplitude * (METER_CEILING_DB - METER_FLOOR_DB)

        // These are post-fader meters, so pulling a fader down has to
        // pull its bar down with it. The fader's contribution is clamped
        // rather than applied in full: the seeded mix sits well below
        // unity, and at face value every bar would start pinned to the
        // floor with nothing to show.
        val fader = max(-24.0, min(0.0, level + 6))
        val db = base + fader

        return if (db <= METER_FLOOR_DB) null else db
    }

    private fun amplitude(voice: Voice, t: Double, offset: Double): Double {
        val bar = (t / BEAT_SECONDS) % 4

        return when (voice) {
            Voice.KICK -> percussive(bar, doubleArrayOf(0.0, 2.0, 2.75), 9.0)
            Voice.SNARE -> percussive(bar, doubleArrayOf(1.0, 3.0), 7.0)
            Voice.HATS -> percussive(
                bar, doubleArrayOf(0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5), 12.0
            ) * 0.65
            // Only around the turnaround, so the toms sit still most of
            // the bar the way a real fill does.
            Voice.TOMS -> percussive(bar, doubleArrayOf(3.5, 3.75), 10.0) * 0.8
            Voice.SUSTAINED -> {
                val slow = sin(t * 0.7 + offset) * 0.5 + 0.5
                val fast = sin(t * 5.3 + offset * 2.1) * 0.5 + 0.5
                min(1.0, 0.45 + 0.35 * slow + 0.15 * fast)
            }
            Voice.SILENT -> 0.0
        }
    }

    /** A decaying hit at each of `hits`, wrapping around the bar so the
     *  last one still rings into the first beat of the next. */
    private fun percussive(bar: Double, hits: DoubleArray, decay: Double): Double {
        var loudest = 0.0

        for (hit in hits) {
            var since = bar - hit
            if (since < 0) since += 4
            loudest = max(loudest, exp(-since * decay))
        }

        return loudest
    }

    private fun deliver(message: JSONObject) {
        MixerClient.deliverDemoMessage(message.toString())
    }

    // Matches the real server's own push cadences. Meters get their own,
    // faster loop (METER_PUSH_INTERVAL_SECONDS in remote_server.py) - at
    // the levels push's 150ms a meter reads as a row of steps rather
    // than a moving bar.
    private const val LEVEL_PUSH_MS = 150L
    private const val METER_PUSH_MS = 50L

    // 120bpm, which is where most of a soundcheck lives.
    private const val BEAT_SECONDS = 60.0 / 120.0

    // The meter's own floor - repeated as a literal rather than
    // referenced, so this file stays free of any dependency on the view
    // layer.
    private const val METER_FLOOR_DB = -60.0
    private const val METER_CEILING_DB = -3.0
}
