package me.pognerebko.vagcompanion;

import static org.junit.Assert.assertFalse;
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
}
