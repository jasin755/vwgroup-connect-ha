package me.pognerebko.vagcompanion;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class AgentHttpServerTest {
    @Test
    public void acceptsLongUrlSafeTokens() {
        assertTrue(AgentHttpServer.isValidToken(
                "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"));
        assertTrue(AgentHttpServer.isValidToken(
                "abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJKLMNOPQRSTUVWXYZ-0123456789"));
    }

    @Test
    public void rejectsShortOrShellSensitiveTokens() {
        assertFalse(AgentHttpServer.isValidToken("short"));
        assertFalse(AgentHttpServer.isValidToken(
                "abcdefghijklmnopqrstuvwxyz0123456789'"));
    }

    @Test
    public void validatesBatteryPercentage() {
        assertEquals(0, VagAccessibilityService.normalizeBatteryLevel(0));
        assertEquals(73, VagAccessibilityService.normalizeBatteryLevel(73));
        assertEquals(100, VagAccessibilityService.normalizeBatteryLevel(100));
        assertEquals(-1, VagAccessibilityService.normalizeBatteryLevel(-1));
        assertEquals(-1, VagAccessibilityService.normalizeBatteryLevel(101));
    }

    @Test
    public void relayEndpointFallsBackToTokenDiscovery() throws Exception {
        assertEquals(
                "/api/vag_connect/companion_agent/by-token",
                AgentRelayClient.endpointPath(""));
        assertEquals(
                "/api/vag_connect/companion_agent/entry-id",
                AgentRelayClient.endpointPath("entry-id"));
    }
}
