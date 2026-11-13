package lanmc.pauseactions;

import com.mojang.logging.LogUtils;
import lanmc.pauseactions.mixin.ButtonActionAccessor;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.PauseScreen;
import net.minecraft.client.gui.screens.social.SocialInteractionsScreen;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.contents.TranslatableContents;
import net.minecraft.server.MinecraftServer.MultiplayerScope;
import net.minecraft.server.commands.PublishCommand;
import net.minecraft.util.HttpUtil;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.EventPriority;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.client.event.ScreenEvent;
import org.slf4j.Logger;

/** Reuses the native pause-menu cell for LAN publication and player reporting. */
@Mod(value = PauseActions.MOD_ID, dist = Dist.CLIENT)
@EventBusSubscriber(modid = PauseActions.MOD_ID, value = Dist.CLIENT)
public final class PauseActions {
    public static final String MOD_ID = "lanmc_pause_actions";
    private static final String ORIGINAL_KEY = "options.worldOptions.button";
    private static final String LAN_KEY = "menu.multiplayerOptions.button";
    private static final String REPORTS_KEY = "menu.playerReporting";
    private static final Logger LOGGER = LogUtils.getLogger();

    public PauseActions() {
    }

    @SubscribeEvent(priority = EventPriority.LOWEST)
    public static void onScreenInit(ScreenEvent.Init.Post event) {
        if (!(event.getScreen() instanceof PauseScreen pause)) {
            return;
        }
        for (var listener : event.getListenersList()) {
            if (listener instanceof Button button
                    && button.getMessage().getContents() instanceof TranslatableContents contents
                    && ORIGINAL_KEY.equals(contents.getKey())) {
                var action = new PauseAction(pause);
                ((ButtonActionAccessor) button).lanmc$setOnPress(action);
                action.refresh(button);
            }
        }
    }

    @SubscribeEvent(priority = EventPriority.LOWEST)
    public static void onScreenRender(ScreenEvent.Render.Pre event) {
        if (!(event.getScreen() instanceof PauseScreen pause)) {
            return;
        }
        for (var listener : pause.children()) {
            if (listener instanceof Button button
                    && ((ButtonActionAccessor) button).lanmc$getOnPress() instanceof PauseAction action
                    && action.parent == pause) {
                action.refresh(button);
            }
        }
    }

    private static final class PauseAction implements Button.OnPress {
        private final PauseScreen parent;

        private PauseAction(PauseScreen parent) {
            this.parent = parent;
        }

        private void refresh(Button button) {
            IntegratedServer server = Minecraft.getInstance().getSingleplayerServer();
            String key = server != null && !server.isPublished() ? LAN_KEY : REPORTS_KEY;
            if (!(button.getMessage().getContents() instanceof TranslatableContents contents)
                    || !key.equals(contents.getKey())) {
                button.setMessage(Component.translatable(key));
            }
            button.active = true;
        }

        @Override
        public void onPress(Button button) {
            Minecraft minecraft = Minecraft.getInstance();
            if (minecraft.gui.screen() != parent) {
                return;
            }
            IntegratedServer server = minecraft.getSingleplayerServer();
            if (server == null || server.isPublished()) {
                minecraft.gui.setScreen(new SocialInteractionsScreen(parent));
                return;
            }

            button.active = false;
            boolean published = false;
            int port = 0;
            try {
                try {
                    port = HttpUtil.getAvailablePort();
                    published = server.publishServer(MultiplayerScope.LAN, port);
                    if (published) {
                        // World commands enable host permissions. Guests need the
                        // separate session flag to get Minecraft's gamemaster permissions.
                        server.setGuestCommandAccess(server.getWorldData().isAllowCommands());
                    }
                } catch (RuntimeException failure) {
                    LOGGER.error("Native LAN publication failed", failure);
                }
                if (published) {
                    SteamWorldDiscovery.published(minecraft, server, port);
                }
                Component message = published
                        ? PublishCommand.getSuccessMessage(port)
                        : Component.translatable("commands.publish.failed");
                minecraft.gui.hud.getChat().addClientSystemMessage(message);
                minecraft.getNarrator().saySystemQueued(message);
                minecraft.updateTitle();
            } finally {
                if (minecraft.gui.screen() == parent) {
                    minecraft.gui.setScreen(null);
                }
            }
        }
    }
}
