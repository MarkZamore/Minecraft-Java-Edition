package proof.exec;

import java.io.File;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.zip.ZipFile;
import lanmc.pauseactions.PauseActions;
import lanmc.pauseactions.mixin.ButtonActionAccessor;
import net.minecraft.SharedConstants;
import net.minecraft.client.*;
import net.minecraft.client.gui.*;
import net.minecraft.client.gui.components.*;
import net.minecraft.client.gui.components.events.GuiEventListener;
import net.minecraft.client.gui.layouts.GridLayout;
import net.minecraft.client.gui.screens.*;
import net.minecraft.client.gui.screens.social.SocialInteractionsScreen;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.multiplayer.*;
import net.minecraft.client.server.IntegratedPlayerList;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.contents.TranslatableContents;
import net.minecraft.server.Bootstrap;
import net.minecraft.server.MinecraftServer.MultiplayerScope;
import net.minecraft.server.packs.PackType;
import net.minecraft.server.packs.metadata.pack.*;
import net.minecraft.server.permissions.LevelBasedPermissionSet;
import net.minecraft.server.players.NameAndId;
import net.minecraft.server.players.ServerOpList;
import net.minecraft.world.level.storage.WorldData;
import net.neoforged.neoforge.client.event.ScreenEvent;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import sun.misc.Unsafe;

/** Actual native types; only publication, navigation and feedback cross recording boundaries. */
public final class ExecutableProbe {
    private static final Unsafe UNSAFE = unsafe();
    private static final String ORIGINAL = "options.worldOptions.button", LAN = "menu.multiplayerOptions.button", REPORTS = "menu.playerReporting";
    private static int assertions;

    public static Map<String, Object> run(boolean transformed, String[] args) throws Exception {
        SharedConstants.tryDetectVersion();
        AtomicInteger original = new AtomicInteger();
        Button baseline = button(Component.translatable(ORIGINAL), ignored -> original.incrementAndGet());
        if (!transformed) {
            check(!(baseline instanceof ButtonActionAccessor), "untransformed native Button must lack the production accessor");
            check(Modifier.isFinal(Button.class.getDeclaredField("onPress").getModifiers()), "untransformed native field is final");
            press(baseline); check(original.get() == 1, "untransformed native action dispatch works");
            return Map.of("nativeFinalField", true, "originalActionDispatch", true);
        }
        check(baseline instanceof ButtonActionAccessor && !Modifier.isFinal(Button.class.getDeclaredField("onPress").getModifiers()),
                "actual production Mixin must inject accessor and make native callback mutable");
        Bootstrap.bootStrap();
        Path jar = Path.of(args[1]);
        Component nativeCaption = nativeContract(jar);
        metadata(jar, args);
        boolean helperAbsent = args.length > 6 && args[6].equals("discovery-helper-absent");
        Class<?> discovery = null;
        try { discovery = Class.forName("minecraft.portable.identity.PortableSteamDiscoveryHooks", false, ClassLoader.getSystemClassLoader()); }
        catch (ClassNotFoundException expected) { check(helperAbsent, "discovery fixture unexpectedly missing"); }
        check(helperAbsent == (discovery == null), "fresh-JVM missing-helper mode has no discoverable helper");
        if (discovery != null) check((Integer) discovery.getField("calls").get(null) == 0, "Object-only discovery recorder starts unused");
        Field singleton = field(Minecraft.class, "instance");
        Object previous = singleton.get(null);
        check(previous == null, "offline probe starts without a constructed Minecraft client");
        RecordingMinecraft minecraft = allocate(RecordingMinecraft.class);
        RecordingGui gui = allocate(RecordingGui.class);
        RecordingChat chat = allocate(RecordingChat.class); chat.messages = new ArrayList<>();
        RecordingNarrator narrator = allocate(RecordingNarrator.class); narrator.messages = new ArrayList<>();
        Hud hud = allocate(Hud.class); set(hud, "chat", chat);
        set(gui, "hud", hud); set(minecraft, "gui", gui); set(minecraft, "narrator", narrator);
        ClientPacketListener connection = allocate(ClientPacketListener.class);
        Map<UUID, PlayerInfo> players = new HashMap<>(); players.put(new UUID(0, 1), null);
        set(connection, "playerInfoMap", players);
        ServerData serverData = allocate(ServerData.class); serverData.name = "Fixture peer";
        set(connection, "serverData", serverData); minecraft.connection = connection;
        RecordingServer server = allocate(RecordingServer.class); server.minecraft = minecraft;
        scope(server, MultiplayerScope.OFF); set(server, "motd", "Fixture host");
        var host = new NameAndId(new UUID(0, 1), "Host");
        var guest = new NameAndId(new UUID(0, 2), "Guest");
        server.setSingleplayerProfile(new com.mojang.authlib.GameProfile(host.id(), host.name()));
        var playerList = allocate(IntegratedPlayerList.class);
        set(playerList, "server", server);
        set(playerList, "playersView", List.of());
        set(playerList, "ops", new ServerOpList(new File("unused-ops.json"), null));
        set(server, "playerList", playerList);
        set(server, "worldData", Proxy.newProxyInstance(WorldData.class.getClassLoader(), new Class<?>[] {WorldData.class},
                (proxy, method, arguments) -> {
                    if (method.getName().equals("isAllowCommands")) return server.worldCommands;
                    throw new AssertionError("Unexpected access to world settings: " + method.getName());
                }));
        set(minecraft, "singleplayerServer", server); set(minecraft, "isLocalServer", true);
        singleton.set(null, minecraft);
        try {
            check(Minecraft.getInstance() == minecraft && minecraft.gui == gui, "actual singleton getter exposes recording boundary");
            check(minecraft.getCurrentServer() == serverData, "actual getCurrentServer executes through the explicit connection boundary");
            check(!server.isPublished(), "native isPublished reads OFF as unpublished");
            Cell cell = cell(nativeCaption, original, 40, 60);
            current(gui, cell.pause);
            List<?> children = List.copyOf(cell.pause.children()), render = List.copyOf(list(cell.pause, "renderables")), narration = List.copyOf(list(cell.pause, "narratables"));
            int[] geometry = geometry(cell.world);
            cell.pause.setFocused(cell.world);
            var event = post(cell.pause);
            PauseActions.onScreenInit(event);
            Button.OnPress action = accessor(cell.world).lanmc$getOnPress();
            check(action.getClass().getName().equals("lanmc.pauseactions.PauseActions$PauseAction"), "actual packaged callback is installed by actual Post handler");
            check(key(cell.world).equals(LAN) && cell.world.active, "unpublished host sees active native LAN caption");
            check(cell.pause.children().equals(children) && list(cell.pause, "renderables").equals(render) && list(cell.pause, "narratables").equals(narration), "all native screen lists retain exact identity/order");
            check(gridWidgets(cell.grid).equals(cell.layoutOrder) && Arrays.equals(geometry, geometry(cell.world)) && cell.pause.getFocused() == cell.world && cell.world.isFocused(), "same native GridLayout cell, geometry and focus are preserved");
            check(cell.literal.getMessage().getContents().getClass() != TranslatableContents.class && accessor(cell.literal).lanmc$getOnPress() == cell.literalAction, "literal matching key is untouched");
            check(key(cell.near).equals(ORIGINAL + ".extra"), "near-key translated control is untouched");
            PauseActions.onScreenInit(event);
            check(accessor(cell.world).lanmc$getOnPress() == action, "repeated Post retains callback identity");

            scope(server, MultiplayerScope.LAN); render(cell.pause);
            check(server.isPublished() && key(cell.world).equals(REPORTS), "actual Render.Pre reads native published state and switches same button to Reports");
            scope(server, MultiplayerScope.OFF); render(cell.pause);
            check(!server.isPublished() && key(cell.world).equals(LAN), "actual Render.Pre returns external unpublished state to LAN");
            check(Arrays.equals(geometry, geometry(cell.world)) && cell.pause.getFocused() == cell.world, "render refresh preserves geometry/focus");

            server.worldCommands = true;
            check(server.getProfilePermissions(host) == LevelBasedPermissionSet.OWNER
                    && server.getProfilePermissions(guest) == LevelBasedPermissionSet.ALL,
                    "before publication only the host can use commands");
            server.succeed = true;
            press(cell.world);
            check(server.attempts == 1 && server.lastScope == MultiplayerScope.LAN && server.lastPort > 0 && server.otherOverloadCalls == 0, "publication invokes only actual native two-argument LAN descriptor");
            check(server.isPublished() && gui.screen() == null && original.get() == 0, "success publishes and closes pause without invoking original WorldOptions action");
            check(server.getGuestCommandAccess() && server.getProfilePermissions(host) == LevelBasedPermissionSet.OWNER
                    && server.getProfilePermissions(guest) == LevelBasedPermissionSet.GAMEMASTER,
                    "native permission resolution lets guests use gameplay commands while retaining host-only privileges");
            check(chat.messages.size() == 1 && chat.messages.getLast().equals(net.minecraft.server.commands.PublishCommand.getSuccessMessage(server.lastPort))
                    && narrator.messages.getLast() == chat.messages.getLast() && minecraft.titles == 1, "native success-message factory reaches chat/narrator/title boundaries");
            if (discovery != null) {
                check((Integer) discovery.getField("calls").get(null) == 1, "successful native callback notifies discovery exactly once");
                check(discovery.getField("minecraft").get(null) == minecraft && discovery.getField("server").get(null) == server
                        && (Integer) discovery.getField("port").get(null) == server.lastPort, "discovery receives the exact successfully published client/server/port");
            } else check(server.isPublished(), "missing helper preserves successful hosting and feedback");
            press(cell.world); check(server.attempts == 1, "stale callback cannot publish a second time");
            current(gui, cell.pause); render(cell.pause);
            check(key(cell.world).equals(REPORTS), "reopened published host sees Reports in the same cell");
            press(cell.world);
            check(gui.screen() instanceof SocialInteractionsScreen && server.attempts == 1, "published host invokes actual native Reports screen constructor without publication");
            SocialInteractionsScreen hostReports = (SocialInteractionsScreen) gui.screen();
            check(get(hostReports, "lastScreen") == cell.pause, "native host Reports screen preserves pause parent");
            hostReports.onClose(); check(gui.screen() == cell.pause, "actual Reports onClose returns through recording Gui navigation boundary");

            set(minecraft, "singleplayerServer", null); set(minecraft, "isLocalServer", false); render(cell.pause);
            check(key(cell.world).equals(REPORTS), "guest sees Reports, never Open to LAN");
            press(cell.world);
            check(gui.screen() instanceof SocialInteractionsScreen && get(gui.screen(), "lastScreen") == cell.pause && server.attempts == 1, "guest opens actual native Reports with parent and no publication attempt");
            int navigationCount = gui.navigations;
            press(cell.world); check(gui.navigations == navigationCount, "stale guest action does not replace Reports");

            set(minecraft, "singleplayerServer", server); set(minecraft, "isLocalServer", true); scope(server, MultiplayerScope.OFF);
            server.setGuestCommandAccess(false);
            Cell resized = cell(nativeCaption, original, 140, 180); current(gui, resized.pause);
            int[] resizedGeometry = geometry(resized.world); PauseActions.onScreenInit(post(resized.pause));
            check(key(resized.world).equals(LAN) && Arrays.equals(resizedGeometry, geometry(resized.world)) && gridWidgets(resized.grid).equals(resized.layoutOrder), "fresh native layout/reinitialization at new geometry preserves its exact cell");
            press(cell.world); check(server.attempts == 1 && gui.screen() == resized.pause, "previous pause instance cannot affect the rebuilt pause");
            server.succeed = false;
            press(resized.world);
            check(server.attempts == 2 && !server.isPublished() && gui.screen() == null && original.get() == 0, "native publication false closes pause without exposing WorldOptions or changing settings");
            check(!server.getGuestCommandAccess(), "refused publication does not enable guest commands");
            check(key(chat.messages.getLast()).equals("commands.publish.failed") && narrator.messages.getLast() == chat.messages.getLast(), "native failure feedback reaches chat and narrator");
            current(gui, resized.pause); render(resized.pause);
            check(resized.world.active && key(resized.world).equals(LAN), "failed publication can be retried after reopening");
            server.throwPublication = true; press(resized.world); server.throwPublication = false;
            check(server.attempts == 3 && gui.screen() == null && !server.isPublished() && original.get() == 0, "publication exception also closes pause without world settings");
            check(!server.getGuestCommandAccess(), "throwing publication does not enable guest commands");
            current(gui, resized.pause); render(resized.pause); chat.failNext = true;
            boolean feedbackFailed = false;
            try { press(resized.world); } catch (IllegalStateException expected) { feedbackFailed = true; }
            check(feedbackFailed && gui.screen() == null && server.attempts == 4, "feedback failure still executes native-policy finally navigation");
            current(gui, resized.pause); render(resized.pause);
            OtherScreen replacement = screen(OtherScreen.class); server.replacement = replacement;
            press(resized.world); server.replacement = null;
            check(gui.screen() == replacement && server.attempts == 5, "publication cannot close a replacement screen");
            OtherScreen other = screen(OtherScreen.class); Button otherWorld = button(nativeCaption, ignored -> original.incrementAndGet()); add(other, otherWorld);
            Button.OnPress otherAction = accessor(otherWorld).lanmc$getOnPress();
            PauseActions.onScreenInit(post(other)); render(other);
            check(accessor(otherWorld).lanmc$getOnPress() == otherAction && key(otherWorld).equals(ORIGINAL), "non-pause screen with exact native caption is untouched");
            check(server.otherOverloadCalls == 0 && original.get() == 0, "no settings-changing overload or original WorldOptions callback executed");
            check(!(gui.screen() instanceof WorldOptionsScreen), "final navigation never exposes WorldOptions");
            if (discovery != null) {
                check((Integer) discovery.getField("calls").get(null) == 1, "stale, published-host, guest, refused and exceptional paths do not notify discovery");
                discovery.getField("fail").set(null, true);
                scope(server, MultiplayerScope.OFF); server.succeed = true;
                current(gui, resized.pause); render(resized.pause);
                int messagesBefore = chat.messages.size(), titlesBefore = minecraft.titles;
                press(resized.world);
                check(server.isPublished() && server.attempts == 6 && gui.screen() == null, "throwing discovery helper cannot undo successful native publication or its navigation");
                check(chat.messages.size() == messagesBefore + 1 && narrator.messages.getLast() == chat.messages.getLast()
                        && minecraft.titles == titlesBefore + 1 && chat.messages.getLast().equals(net.minecraft.server.commands.PublishCommand.getSuccessMessage(server.lastPort)), "throwing discovery helper preserves native success feedback");
                check((Integer) discovery.getField("calls").get(null) == 2 && discovery.getField("server").get(null) == server
                        && (Integer) discovery.getField("port").get(null) == server.lastPort, "throwing optional helper was reached exactly once with the current publication");
                discovery.getField("fail").set(null, false);
            }
            scope(server, MultiplayerScope.OFF);
            server.worldCommands = false;
            server.setGuestCommandAccess(true);
            server.succeed = true;
            current(gui, resized.pause); render(resized.pause); press(resized.world);
            check(server.isPublished() && !server.getGuestCommandAccess()
                    && server.getProfilePermissions(host) == LevelBasedPermissionSet.ALL
                    && server.getProfilePermissions(guest) == LevelBasedPermissionSet.ALL,
                    "sharing a commands-disabled world clears stale guest access and grants commands to neither player");
        } finally { singleton.set(null, previous); }
        check(Minecraft.getInstance() == previous, "actual singleton restored");
        var execution = new LinkedHashMap<String, Object>();
        execution.putAll(Map.of("assertions", assertions, "actualProductionPostAndRender", true, "actualNativeGridButtonIdentityGeometryFocus", true,
                "actualNativePublishedState", true, "actualSocialScreenConstructorAndOnClose", true,
                "worldSettingsActionCalls", original.get(), "recordingBoundaries", "publish(scope,int), Gui.setScreen, chat, narration, title and connection getter",
                "gameMainStarted", false, "rendererStarted", false));
        execution.put("discoveryNotification", helperAbsent ? "missing helper preserves hosting and feedback" : "exact success-only Object API notification; throwing helper preserves hosting and feedback");
        execution.put("nativeHostAndGuestPermissions", "commands on/off; rejected and throwing publication");
        return execution;
    }

    private static Component nativeContract(Path jar) throws Exception {
        Field world = field(PauseScreen.class, "WORLD_OPTIONS"); Component caption = (Component) world.get(null);
        check(key(caption).equals(ORIGINAL), "actual native WORLD_OPTIONS translation key must match contract");
        ClassNode pause = node(PauseScreen.class), integrated = node(IntegratedServer.class);
        MethodNode menu = pause.methods.stream().filter(m -> m.name.equals("createPauseMenu") && m.desc.equals("()V")).findFirst().orElseThrow();
        List<AbstractInsnNode> instructions = ops(menu);
        List<FieldInsnNode> uses = instructions.stream().filter(i -> i instanceof FieldInsnNode f && f.getOpcode() == Opcodes.GETSTATIC && f.owner.equals(pause.name) && f.name.equals("WORLD_OPTIONS")).map(i -> (FieldInsnNode)i).toList();
        check(uses.size() == 1, "native pause has exactly one WorldOptions field producer");
        int index = instructions.indexOf(uses.getFirst());
        check(instructions.get(index + 2) instanceof InvokeDynamicInsnNode && instructions.get(index + 3) instanceof MethodInsnNode call && call.name.equals("openScreenButton") && call.desc.equals("(Lnet/minecraft/network/chat/Component;Ljava/util/function/Supplier;)Lnet/minecraft/client/gui/components/Button;"), "actual field remains connected to the native Button/Supplier cell producer");
        InvokeDynamicInsnNode indy = (InvokeDynamicInsnNode) instructions.get(index + 2); Handle handle = (Handle) indy.bsmArgs[1];
        MethodNode supplier = pause.methods.stream().filter(m -> m.name.equals(handle.getName()) && m.desc.equals(handle.getDesc())).findFirst().orElseThrow();
        check(ops(supplier).stream().anyMatch(i -> i instanceof TypeInsnNode allocation && allocation.getOpcode() == Opcodes.NEW && allocation.desc.equals("net/minecraft/client/gui/screens/WorldOptionsScreen")), "native original action is proven to construct WorldOptionsScreen");
        verifyNativeProducer(pause);
        check(true, "complete actual native field/Button/Supplier/WorldOptions constructor chain is proven");
        ClassNode missingProducer = node(PauseScreen.class);
        MethodNode missingMenu = named(missingProducer, "createPauseMenu", "()V");
        missingMenu.instructions.remove(worldUses(missingMenu).getFirst());
        expectRejected(() -> verifyNativeProducer(missingProducer), "unique native WorldOptions field", "removed native producer must fail the gate");
        ClassNode changedProducer = node(PauseScreen.class);
        MethodNode changedMenu = named(changedProducer, "createPauseMenu", "()V");
        var changedOps = ops(changedMenu);
        var changedIndy = (InvokeDynamicInsnNode) changedOps.get(changedOps.indexOf(worldUses(changedMenu).getFirst()) + 2);
        var changedHandle = (Handle) changedIndy.bsmArgs[1];
        ((TypeInsnNode)ops(named(changedProducer, changedHandle.getName(), changedHandle.getDesc())).getFirst()).desc = "net/minecraft/client/gui/screens/options/OptionsScreen";
        expectRejected(() -> verifyNativeProducer(changedProducer), "native WorldOptions constructor chain", "changed native target screen must fail the gate");
        MethodNode published = integrated.methods.stream().filter(m -> m.name.equals("isPublished") && m.desc.equals("()Z")).findFirst().orElseThrow();
        List<AbstractInsnNode> publishedOps = ops(published);
        check(publishedOps.size() == 8 && publishedOps.get(1) instanceof FieldInsnNode state && state.name.equals("multiplayerScope")
                && publishedOps.get(2) instanceof FieldInsnNode off && off.name.equals("OFF")
                && publishedOps.stream().noneMatch(i -> i instanceof MethodInsnNode || i.getOpcode() == Opcodes.PUTFIELD), "native published state is exactly the scope-vs-OFF comparison, not a fixture override");
        check(!Modifier.isFinal(IntegratedServer.class.getMethod("publishServer", MultiplayerScope.class, int.class).getModifiers()), "native two-argument publication seam exists");
        verifyNativePublication(integrated);
        try (ZipFile zip = new ZipFile(jar.toFile())) {
            int publicationCalls = 0;
            int discoveryCalls = 0;
            boolean discoveryBridge = false;
            boolean forbiddenReference = false;
            for (var entry : zip.stream().filter(e -> e.getName().endsWith(".class")).toList()) {
                ClassNode component = new ClassNode(); new ClassReader(zip.getInputStream(entry)).accept(component, 0);
                if (component.name.equals("lanmc/pauseactions/PauseActions")) {
                    check(clientOnly(annotation(component, "/Mod;"), "dist"), "packaged mod entry point is explicitly client-only");
                    check(clientOnly(annotation(component, "/EventBusSubscriber;"), "value"), "packaged event subscriber is explicitly client-only");
                }
                if (component.name.equals("lanmc/pauseactions/PauseActions$PauseAction")) {
                    verifyDiscoveryNotification(component);
                    MethodNode action = named(component, "onPress", "(Lnet/minecraft/client/gui/components/Button;)V");
                    MethodInsnNode notification = ops(action).stream().filter(i -> i instanceof MethodInsnNode call && call.owner.equals(DISCOVERY)).map(i -> (MethodInsnNode)i).findFirst().orElseThrow();
                    action.instructions.remove(notification);
                    ClassNode missingNotification = component;
                    expectRejected(() -> verifyDiscoveryNotification(missingNotification), "one discovery notification", "removing discovery notification must fail publication even when metadata is retained");
                    ClassNode inverted = new ClassNode(); new ClassReader(zip.getInputStream(entry)).accept(inverted, 0);
                    MethodNode invertedAction = named(inverted, "onPress", "(Lnet/minecraft/client/gui/components/Button;)V");
                    var body = ops(invertedAction); int callIndex = -1;
                    for (int i = 0; i < body.size(); i++) if (body.get(i) instanceof MethodInsnNode call && call.owner.equals(DISCOVERY)) callIndex = i;
                    ((JumpInsnNode)body.get(callIndex - 4)).setOpcode(Opcodes.IFNE);
                    expectRejected(() -> verifyDiscoveryNotification(inverted), "successful publication result", "inverted discovery notification guard must fail publication");
                    // Continue ordinary scanning with the unmodified packaged bytecode.
                    component = new ClassNode(); new ClassReader(zip.getInputStream(entry)).accept(component, 0);
                }
                if (component.name.equals(DISCOVERY)) {
                    discoveryBridge = true;
                    check(component.fields.stream().noneMatch(f -> f.desc.contains("link/e4steam")), "bridge has no direct e4steam field dependency");
                    for (MethodNode method : component.methods) for (AbstractInsnNode instruction : method.instructions) {
                        check(!(instruction instanceof MethodInsnNode call && call.owner.startsWith("link/e4steam/"))
                                && !(instruction instanceof TypeInsnNode type && type.desc.startsWith("link/e4steam/")), "bridge survives transport replacement without a direct e4steam binary dependency");
                    }
                }
                for (MethodNode method : component.methods) for (AbstractInsnNode instruction : method.instructions) {
                    if (instruction instanceof TypeInsnNode type && type.desc.equals("net/minecraft/client/gui/screens/WorldOptionsScreen")) forbiddenReference = true;
                    if (instruction instanceof FieldInsnNode field && (field.owner.equals("net/minecraft/client/gui/screens/WorldOptionsScreen") || field.desc.contains("WorldOptionsScreen;"))) forbiddenReference = true;
                    if (instruction instanceof TypeInsnNode allocation && allocation.getOpcode() == Opcodes.NEW)
                        check(!allocation.desc.equals("net/minecraft/client/gui/screens/WorldOptionsScreen"), "component must not construct WorldOptionsScreen");
                    if (instruction instanceof MethodInsnNode call) {
                        if (call.owner.equals("net/minecraft/client/gui/screens/WorldOptionsScreen") || call.desc.contains("WorldOptionsScreen;")
                                || Set.of("removeWidget", "clearWidgets", "addRenderableWidget", "addWidget", "addRenderableOnly", "removeListener", "addListener").contains(call.name)) forbiddenReference = true;
                        check(!Set.of("setDefaultGameType", "setGameType", "setDifficulty", "setCommandsAllowed", "setAllowCommands", "setWorldAllowCommands", "applyChanges").contains(call.name), "component must not call a world-settings mutator");
                        if (call.name.equals("publishServer")) { publicationCalls++; check(call.desc.equals("(Lnet/minecraft/server/MinecraftServer$MultiplayerScope;I)Z"), "packaged component calls only native two-argument publication"); }
                        if (call.owner.equals(DISCOVERY) && call.name.equals("published")) discoveryCalls++;
                    }
                }
            }
            check(publicationCalls == 1, "component has one exact native publication site");
            check(discoveryBridge && discoveryCalls == 1, "component ships its required optional integration and one discovery notification site");
            check(!forbiddenReference, "component contains no WorldOptions reference or screen-widget removal/addition call");
        }
        return caption;
    }
    private static final String DISCOVERY = "lanmc/pauseactions/SteamWorldDiscovery";
    private static void verifyDiscoveryNotification(ClassNode action) {
        MethodNode method = named(action, "onPress", "(Lnet/minecraft/client/gui/components/Button;)V");
        var body = ops(method);
        var notifications = body.stream().filter(i -> i instanceof MethodInsnNode call && call.owner.equals(DISCOVERY)).toList();
        require(notifications.size() == 1, "expected one discovery notification in the native callback");
        int index = body.indexOf(notifications.getFirst());
        MethodInsnNode notify = (MethodInsnNode)notifications.getFirst();
        require(notify.getOpcode() == Opcodes.INVOKESTATIC && notify.name.equals("published")
                && notify.desc.equals("(Lnet/minecraft/client/Minecraft;Lnet/minecraft/client/server/IntegratedServer;I)V"), "discovery notification API changed");
        var publications = body.stream().filter(i -> i instanceof MethodInsnNode call && call.name.equals("publishServer")).toList();
        require(publications.size() == 1 && body.indexOf(publications.getFirst()) < index, "discovery notification precedes native publication");
        int nativeIndex = body.indexOf(publications.getFirst());
        require(index >= 5 && body.get(nativeIndex - 3) instanceof VarInsnNode server && server.getOpcode() == Opcodes.ALOAD
                && body.get(nativeIndex - 1) instanceof VarInsnNode port && port.getOpcode() == Opcodes.ILOAD
                && body.get(index - 2) instanceof VarInsnNode sameServer && sameServer.getOpcode() == Opcodes.ALOAD && sameServer.var == server.var
                && body.get(index - 1) instanceof VarInsnNode samePort && samePort.getOpcode() == Opcodes.ILOAD && samePort.var == port.var,
                "discovery notification must retain the exact native server/port arguments");
        require(body.get(nativeIndex + 1) instanceof VarInsnNode result && result.getOpcode() == Opcodes.ISTORE
                && body.get(index - 5) instanceof VarInsnNode test && test.getOpcode() == Opcodes.ILOAD && test.var == result.var
                && body.get(index - 4) instanceof JumpInsnNode guard && guard.getOpcode() == Opcodes.IFEQ
                && method.instructions.indexOf(guard.label) > method.instructions.indexOf(notify), "discovery notification must be gated by the successful publication result");
    }
    private static final String PAUSE = "net/minecraft/client/gui/screens/PauseScreen", WORLD = "net/minecraft/client/gui/screens/WorldOptionsScreen";
    private static void verifyNativeProducer(ClassNode pause) {
        MethodNode menu = named(pause, "createPauseMenu", "()V");
        var uses = worldUses(menu);
        require(uses.size() == 1, "expected unique native WorldOptions field producer");
        var instructions = ops(menu); int index = instructions.indexOf(uses.getFirst());
        require(index > 0 && index + 3 < instructions.size() && aload0(instructions.get(index - 1)) && aload0(instructions.get(index + 1)), "native receiver/captured parent chain changed");
        require(instructions.get(index + 3) instanceof MethodInsnNode call && call.getOpcode() == Opcodes.INVOKEVIRTUAL && !call.itf
                && call.owner.equals(PAUSE) && call.name.equals("openScreenButton") && call.desc.equals("(Lnet/minecraft/network/chat/Component;Ljava/util/function/Supplier;)Lnet/minecraft/client/gui/components/Button;"), "native Button builder chain changed");
        require(instructions.get(index + 2) instanceof InvokeDynamicInsnNode, "native Supplier factory changed");
        InvokeDynamicInsnNode indy = (InvokeDynamicInsnNode)instructions.get(index + 2);
        require(indy.name.equals("get") && indy.desc.equals("(L" + PAUSE + ";)Ljava/util/function/Supplier;") && indy.bsm.getOwner().equals("java/lang/invoke/LambdaMetafactory")
                && indy.bsm.getName().equals("metafactory") && indy.bsmArgs.length == 3 && indy.bsmArgs[1] instanceof Handle, "native Supplier factory changed");
        Handle handle = (Handle)indy.bsmArgs[1];
        require(handle.getOwner().equals(PAUSE) && handle.getDesc().equals("()Lnet/minecraft/client/gui/screens/Screen;")
                && !handle.isInterface() && (handle.getTag() == Opcodes.H_INVOKEVIRTUAL || handle.getTag() == Opcodes.H_INVOKESPECIAL), "native Supplier implementation changed");
        var body = ops(named(pause, handle.getName(), handle.getDesc()));
        require(body.size() == 8 && body.get(0) instanceof TypeInsnNode allocation && allocation.getOpcode() == Opcodes.NEW && allocation.desc.equals(WORLD)
                && body.get(1).getOpcode() == Opcodes.DUP && aload0(body.get(2)) && aload0(body.get(3))
                && body.get(4) instanceof FieldInsnNode minecraft && minecraft.getOpcode() == Opcodes.GETFIELD && minecraft.owner.equals(PAUSE)
                && minecraft.name.equals("minecraft") && minecraft.desc.equals("Lnet/minecraft/client/Minecraft;")
                && body.get(5) instanceof FieldInsnNode level && level.getOpcode() == Opcodes.GETFIELD && level.owner.equals("net/minecraft/client/Minecraft")
                && level.name.equals("level") && level.desc.equals("Lnet/minecraft/client/multiplayer/ClientLevel;")
                && body.get(6) instanceof MethodInsnNode constructor && constructor.getOpcode() == Opcodes.INVOKESPECIAL && constructor.owner.equals(WORLD)
                && constructor.name.equals("<init>") && constructor.desc.equals("(Lnet/minecraft/client/gui/screens/Screen;Lnet/minecraft/world/level/Level;)V")
                && body.get(7).getOpcode() == Opcodes.ARETURN, "native WorldOptions constructor chain changed");
    }
    private static void verifyNativePublication(ClassNode integrated) {
        MethodNode publish = named(integrated, "publishServer", "(Lnet/minecraft/server/MinecraftServer$MultiplayerScope;I)Z");
        var instructions = ops(publish);
        var tcp = instructions.stream().filter(i -> i instanceof MethodInsnNode call && call.owner.equals("net/minecraft/server/network/ServerConnectionListener")
                && call.name.equals("startTcpServerListener") && call.desc.equals("(Ljava/net/InetAddress;I)V") && call.getOpcode() == Opcodes.INVOKEVIRTUAL).toList();
        check(tcp.size() == 1, "native two-argument publication has one exact TCP listener bridge");
        int index = instructions.indexOf(tcp.getFirst());
        check(index >= 4 && aload0(instructions.get(index - 4)) && instructions.get(index - 3) instanceof MethodInsnNode connection
                && connection.getOpcode() == Opcodes.INVOKEVIRTUAL && connection.name.equals("getConnection") && connection.desc.equals("()Lnet/minecraft/server/network/ServerConnectionListener;")
                && instructions.get(index - 2).getOpcode() == Opcodes.ACONST_NULL && instructions.get(index - 1) instanceof VarInsnNode port
                && port.getOpcode() == Opcodes.ILOAD && port.var == 2, "native bridge uses native server connection and original port argument");
        boolean mutatesSettings = false;
        for (AbstractInsnNode instruction : instructions) {
            if (instruction instanceof MethodInsnNode call && (call.owner.contains("WorldData") || call.owner.contains("GameRules")
                    || Set.of("setDefaultGameType", "setGameType", "setDifficulty", "setCommandsAllowed", "setAllowCommands", "applyChanges").contains(call.name))) mutatesSettings = true;
            if (instruction instanceof FieldInsnNode field && field.getOpcode() == Opcodes.PUTFIELD
                    && !(field.owner.equals(integrated.name) && Set.of("publishedPort", "lanPinger").contains(field.name))) mutatesSettings = true;
        }
        check(!mutatesSettings, "native two-argument publication contains no world-data/commands/game-mode mutator and only publication field writes");
    }
    private static MethodNode named(ClassNode node, String name, String descriptor) {
        var methods = node.methods.stream().filter(method -> method.name.equals(name) && method.desc.equals(descriptor)).toList();
        require(methods.size() == 1, "native method identity changed: " + name + descriptor); return methods.getFirst();
    }
    private static List<FieldInsnNode> worldUses(MethodNode method) {
        return ops(method).stream().filter(i -> i instanceof FieldInsnNode field && field.getOpcode() == Opcodes.GETSTATIC && field.owner.equals(PAUSE)
                && field.name.equals("WORLD_OPTIONS") && field.desc.equals("Lnet/minecraft/network/chat/Component;")).map(i -> (FieldInsnNode)i).toList();
    }
    private static boolean aload0(AbstractInsnNode instruction) { return instruction instanceof VarInsnNode variable && variable.getOpcode() == Opcodes.ALOAD && variable.var == 0; }
    private static void require(boolean condition, String message) { if (!condition) throw new AssertionError(message); }
    private static void expectRejected(Runnable validation, String reason, String message) {
        boolean rejected = false; try { validation.run(); } catch (AssertionError failure) { rejected = failure.getMessage().contains(reason); } check(rejected, message);
    }
    private static AnnotationNode annotation(ClassNode node, String suffix) {
        var annotations = new ArrayList<AnnotationNode>();
        if (node.visibleAnnotations != null) annotations.addAll(node.visibleAnnotations);
        if (node.invisibleAnnotations != null) annotations.addAll(node.invisibleAnnotations);
        var found = annotations.stream().filter(a -> a.desc.endsWith(suffix)).toList();
        check(found.size() == 1, "one exact client-scope annotation " + suffix);
        return found.getFirst();
    }
    private static boolean clientOnly(AnnotationNode annotation, String key) {
        for (int i = 0; annotation.values != null && i < annotation.values.size(); i += 2)
            if (annotation.values.get(i).equals(key)) return annotation.values.get(i + 1) instanceof List<?> values && values.size() == 1
                    && values.getFirst() instanceof String[] value && value[0].endsWith("/Dist;") && value[1].equals("CLIENT");
        return false;
    }
    private static void metadata(Path jar, String[] args) throws Exception {
        try (var zip = new ZipFile(jar.toFile())) {
            var pack = com.google.gson.JsonParser.parseString(new String(zip.getInputStream(zip.getEntry("pack.mcmeta")).readAllBytes(), StandardCharsets.UTF_8)).getAsJsonObject().get("pack");
            for (var pair : List.of(Map.entry(PackType.CLIENT_RESOURCES, new PackFormat(Integer.parseInt(args[2]), Integer.parseInt(args[3]))), Map.entry(PackType.SERVER_DATA, new PackFormat(Integer.parseInt(args[4]), Integer.parseInt(args[5]))))) {
                var result = PackMetadataSection.forPackType(pair.getKey()).codec().parse(com.mojang.serialization.JsonOps.INSTANCE, pack);
                check(result.result().orElseThrow().supportedFormats().isValueInRange(pair.getValue()), "actual native pack metadata codec accepts current format");
            }
        }
    }
    private record Cell(PauseScreen pause, GridLayout grid, Button world, Button literal, Button.OnPress literalAction, Button near, List<AbstractWidget> layoutOrder) { }
    private static Cell cell(Component caption, AtomicInteger original, int x, int y) throws Exception {
        PauseScreen pause = screen(PauseScreen.class);
        GridLayout grid = new GridLayout(); var rows = grid.createRowHelper(2);
        Button before = button(Component.translatable("menu.options"), ignored -> { });
        Button world = button(caption, ignored -> original.incrementAndGet());
        Button.OnPress literalAction = ignored -> { };
        Button literal = button(Component.literal(ORIGINAL), literalAction);
        Button near = button(Component.translatable(ORIGINAL + ".extra"), ignored -> { });
        rows.addChild(before); rows.addChild(world); rows.addChild(literal); rows.addChild(near);
        grid.setX(x); grid.setY(y); grid.arrangeElements();
        for (AbstractWidget widget : gridWidgets(grid)) add(pause, widget);
        return new Cell(pause, grid, world, literal, literalAction, near, gridWidgets(grid));
    }
    private static List<AbstractWidget> gridWidgets(GridLayout grid) { var widgets = new ArrayList<AbstractWidget>(); grid.visitWidgets(widgets::add); return widgets; }
    private static Button button(Component caption, Button.OnPress action) { return Button.builder(caption, action).bounds(0, 0, 98, 20).build(); }
    private static void press(Button button) { button.onPress(new KeyEvent(13, 0, 0)); }
    private static ButtonActionAccessor accessor(Button button) { return (ButtonActionAccessor) button; }
    private static String key(Button button) { return key(button.getMessage()); }
    private static String key(Component caption) { return ((TranslatableContents) caption.getContents()).getKey(); }
    private static int[] geometry(Button button) { return new int[]{button.getX(), button.getY(), button.getWidth(), button.getHeight()}; }
    @SuppressWarnings("unchecked") private static ScreenEvent.Init.Post post(Screen screen) {
        return new ScreenEvent.Init.Post(screen, (List<GuiEventListener>) screen.children(), widget -> add(screen, widget), widget -> { throw new AssertionError("in-place policy must not remove native controls"); });
    }
    private static void render(Screen screen) { PauseActions.onScreenRender(new ScreenEvent.Render.Pre(screen, null, 0, 0, 0)); }
    private static void add(Screen screen, GuiEventListener widget) {
        try { Method method = Screen.class.getDeclaredMethod("addRenderableWidget", GuiEventListener.class); method.setAccessible(true); method.invoke(screen, widget); }
        catch (ReflectiveOperationException failure) { throw new AssertionError(failure); }
    }
    private static <T extends Screen> T screen(Class<T> type) throws Exception {
        T screen = allocate(type); for (String name : List.of("children", "renderables", "narratables")) set(screen, name, new ArrayList<>());
        screen.width = 400; screen.height = 300; return screen;
    }
    private static List<?> list(Screen screen, String name) throws Exception { return (List<?>) get(screen, name); }
    private static void current(RecordingGui gui, Screen screen) { set(gui, "screen", screen); }
    private static void scope(RecordingServer server, MultiplayerScope scope) { set(server, "multiplayerScope", scope); }
    private static Object get(Object target, String name) throws Exception { return field(target.getClass(), name).get(target); }
    private static void set(Object target, String name, Object value) { try { field(target.getClass(), name).set(target, value); } catch (ReflectiveOperationException failure) { throw new AssertionError(failure); } }
    private static Field field(Class<?> type, String name) throws NoSuchFieldException {
        for (Class<?> current = type; current != null; current = current.getSuperclass()) try { Field field = current.getDeclaredField(name); field.setAccessible(true); return field; } catch (NoSuchFieldException ignored) { }
        throw new NoSuchFieldException(type.getName() + "." + name);
    }
    private static <T> T allocate(Class<T> type) throws InstantiationException { return type.cast(UNSAFE.allocateInstance(type)); }
    private static Unsafe unsafe() { try { Field field = Unsafe.class.getDeclaredField("theUnsafe"); field.setAccessible(true); return (Unsafe) field.get(null); } catch (ReflectiveOperationException failure) { throw new ExceptionInInitializerError(failure); } }
    private static ClassNode node(Class<?> type) throws Exception { try (var stream = type.getResourceAsStream(type.getSimpleName() + ".class")) { ClassNode node = new ClassNode(); new ClassReader(Objects.requireNonNull(stream)).accept(node, 0); return node; } }
    private static List<AbstractInsnNode> ops(MethodNode method) { var result = new ArrayList<AbstractInsnNode>(); for (var insn : method.instructions) if (insn.getOpcode() >= 0) result.add(insn); return result; }
    private static void check(boolean condition, String message) { assertions++; if (!condition) throw new AssertionError(message); }

    private static final class RecordingMinecraft extends Minecraft {
        private ClientPacketListener connection; private int titles;
        RecordingMinecraft() { super(null); }
        @Override public ClientPacketListener getConnection() { return connection; }
        @Override public void updateTitle() { titles++; }
    }
    private static final class RecordingGui extends Gui {
        private int navigations;
        RecordingGui() { super(null, null, null); }
        @Override public void setScreen(Screen screen) { navigations++; current(this, screen); }
    }
    private static final class RecordingChat extends ChatComponent {
        private List<Component> messages; private boolean failNext;
        RecordingChat() { super(null); }
        @Override public void addClientSystemMessage(Component message) { if (failNext) { failNext = false; throw new IllegalStateException("fixture feedback failure"); } messages.add(message); }
    }
    private static final class RecordingNarrator extends GameNarrator {
        private List<Component> messages;
        RecordingNarrator() { super(null); }
        @Override public void saySystemQueued(Component message) { messages.add(message); }
    }
    private static final class RecordingServer extends IntegratedServer {
        private RecordingMinecraft minecraft; private boolean succeed, throwPublication; private Screen replacement;
        private boolean worldCommands;
        private int attempts, lastPort, otherOverloadCalls; private MultiplayerScope lastScope;
        RecordingServer() { super(null, null, null, null, null, null, null, null); }
        @Override public boolean publishServer(MultiplayerScope scope, int port) {
            attempts++; lastScope = scope; lastPort = port;
            if (replacement != null) current((RecordingGui) minecraft.gui, replacement);
            if (throwPublication) throw new IllegalStateException("fixture publication refusal");
            if (succeed) scope(this, scope);
            return succeed;
        }
        @Override public boolean publishServer(MultiplayerScope scope, boolean commands, int port) { otherOverloadCalls++; throw new AssertionError("settings-changing publication overload must not run"); }
    }
    private static final class OtherScreen extends Screen { OtherScreen() { super((Minecraft)null, null, Component.empty()); } }
}
