# Native Pause Actions and actual Mixin engine proof

From the pack source root, with the pack's JDK (currently Java 25):

```text
python tools/pause-actions/verify_native.py --jar path/to/lanmc-pause-actions-1.0.2.jar
```

Use `--report .cache/pause-actions/native-verification.json` for a compact report.
Missing sources/dependencies, malformed metadata, an optional or undeclared
Mixin config, an absent accessor transformation or a failed assertion fail the
command. There is no skipped-check branch. Official Minecraft/NeoForge artifacts
are resolved and hash-checked by `build.py`; temporary classes and diagnostic logs
stay in ignored `.cache/pause-actions`.

The classpath must put NeoForge's officially prepared patched client before raw
vanilla fallback classes. The report records that JAR's SHA256 and the exact
native `Button.class` SHA256/size, then cross-checks these with the bytes actually
passed to Mixin. The known Minecraft 26.3 / NeoForge 26.3.0.99 target is pinned to
`a3c5bf37aac82e360913080768fdf2005506afb9a0124e3466ee0a4d1bf6de7a`
(4265 bytes); the vanilla 3755-byte class is rejected as a substitute.

Patched native constants read the FML environment and mod feature-flag inventory.
The headless host invokes the genuine private `FMLLoader` context constructor
with CLIENT/PROD and a temporary game directory, then installs a genuine empty
`LoadingModList` made by its public factory. This supplies those context reads
without running `FMLLoader.create`, discovery, plugin/mod initialization or an
early display window. Neither FML nor Minecraft classes are replaced by mocks.

The probe consumes the **actual packaged production JAR**, including its
`lanmc-pause-actions.mixins.json` and `ButtonActionAccessor`. It does not compile
an alternative accessor. A small `IMixinService` supplies unmodified class bytes
to the actual NeoForge-selected Sponge `MixinTransformer`. That engine transforms
the actual native `Button.class`; ASM only reads the result to require exactly
one generated field getter and setter. ASM does not generate the transformation.
The original native class is run first to establish its final field and original
callback. The transformed class is then loaded with matching native Minecraft
and NeoForge event classes under `-Xverify:all`. Calls use the generated accessor;
there is no reflective write to `Button.onPress`.
Two fresh JVMs additionally fault-inject a missing `onPress` field and an
incorrect field type into the native input bytes. The actual required Mixin
must reject both with its accessor failure; accepting either fails the CLI.
ASM writes only those invalid negative inputs, never the positive accessor
transformation.

The actual packaged `PauseActions.onScreenInit(Init.Post)` and
`onScreenRender(Render.Pre)` execute against native `Button`, `PauseScreen`,
`GridLayout`, translated components and event objects. Checks preserve the exact
button object, grid widget order/cell geometry, child/render/narration lists and
focus. They cover repeated initialization, fresh native layouts at different
coordinates, host publication state changes, literal/near-key/non-pause
negatives, stale callbacks and a replaced screen.

The real native `IntegratedServer.isPublished()` executes on a constructor-free
server, with ASM also checking its scope-versus-OFF body. Only the network
`publishServer(scope, int)` boundary is overridden to record success, refusal or
exception. The alternate settings-changing overload fails if called. The
component's class files must have one exact two-argument publication site, no
`WorldOptionsScreen` construction and no world-setting mutator calls. The actual
original World Options callback remains uncalled. Native `HttpUtil` briefly
probes a free local port; a Minecraft server listener is never started.
The actual native two-argument publication method must call the exact
`ServerConnectionListener.startTcpServerListener(InetAddress, int)` bridge once,
using its original port argument. Its direct calls may not mutate world data,
commands or game mode; its field writes are restricted to publication state.
The packaged policy must not reference World Options or add/remove screen
widgets.

Native `Minecraft.getCurrentServer()` runs through an explicit recording
connection getter and a constructor-free native `ClientPacketListener`. The real
`SocialInteractionsScreen(parent)` constructor and its native `onClose()` run for
both host and guest. `Gui.setScreen` alone records navigation without initializing
another screen or a renderer. Chat, narration and title update boundaries record
the exact native publication success/failure message. Failure, exception,
feedback failure, retry and stale/replaced-screen paths are exercised. The real
Minecraft singleton is restored in a `finally` block.

The packaged successful-publication callback must notify its own
`SteamWorldDiscovery.published(Minecraft, IntegratedServer, int)` once, with the
same native server and port. The test-only Object API recorder has the existing
launcher's `PortableSteamDiscoveryHooks` name but starts no Steam client. The
probe checks exactly one notification on success, none on refusal, exception,
stale callback, guest or already published host, and preserved native success
feedback/navigation when the optional helper throws. A second fresh JVM runs
with that recorder class physically excluded from its classpath and requires
hosting, feedback and the same menu tests to succeed without any helper.
The recorder is probe input only and is forbidden in the packaged component.

ASM additionally requires one success-result-guarded notification after the
native publication site, using the same server/port locals. Removing that call
or inverting its success condition is rejected even if all packaged metadata
remains present. The bridge may not directly link e4steam classes: updating
the centrally delivered transport cannot leave a baked-in e4steam binary API
inside this pack. Native publication and these discovery checks are mandatory
for every runtime resolved from `portable-pack.json`; no current-version-only
branch can silently skip them after a Minecraft upgrade.

The native `PauseScreen.WORLD_OPTIONS` component and unique
`createPauseMenu` field/Supplier/Button producer are read from the actual runtime.
The exact captured supplier constructor body must create
`WorldOptionsScreen(parent, level)`; removed/changed producer mutations are also
rejected in memory. A changed native translation key or producer fails the check. Packaged
client-only annotations and TOML are checked, and the actual Minecraft client
and server `PackMetadataSection` codecs validate `pack.mcmeta` against the
selected runtime formats.

This is an offline native API/Mixin/action proof. Constructors that require a
running client are bypassed with `Unsafe`; network publication, navigation and
feedback use the explicit boundaries above. It does **not** start a client/server
main, a renderer or a user world, and does not prove full mod-loader discovery,
actual screen resize dispatch, rendered appearance, Steam networking or gameplay.

## РџСЂР°РІР° РєРѕРјР°РЅРґ РїСЂРё РїСѓР±Р»РёРєР°С†РёРё

РџРѕСЃР»Рµ РЅР°Р¶Р°С‚РёСЏ РїСЂРѕРІРµСЂСЏСЋС‚СЃСЏ С€С‚Р°С‚РЅС‹Рµ СѓСЂРѕРІРЅРё РїСЂР°РІ С…РѕСЃС‚Р° Рё РіРѕСЃС‚СЏ Р±РµР· OP С‡РµСЂРµР· IntegratedServer Рё PlayerList. РџРѕРґРјРµРЅР° Р·Р°РґР°С‘С‚ С‚РѕР»СЊРєРѕ СЃРѕС…СЂР°РЅС‘РЅРЅС‹Р№ С„Р»Р°Рі WorldData Рё СЂРµР·СѓР»СЊС‚Р°С‚ СЃРµС‚РµРІРѕР№ РїСѓР±Р»РёРєР°С†РёРё. Р’РєР»СЋС‡С‘РЅРЅС‹Рµ РєРѕРјР°РЅРґС‹ РґР°СЋС‚ OWNER Рё GAMEMASTER, РІС‹РєР»СЋС‡РµРЅРЅС‹Рµ РѕСЃС‚Р°РІР»СЏСЋС‚ РѕР±РѕРёС… Р±РµР· РёРіСЂРѕРІС‹С… РєРѕРјР°РЅРґ. РџСЂРѕРІРµСЂСЏСЋС‚СЃСЏ С‚Р°РєР¶Рµ РѕС‚РєР°Р·, РёСЃРєР»СЋС‡РµРЅРёРµ РїСѓР±Р»РёРєР°С†РёРё Рё СЃР±СЂРѕСЃ СЂР°РЅРµРµ РІРєР»СЋС‡С‘РЅРЅРѕРіРѕ РґРѕСЃС‚СѓРїР°.
