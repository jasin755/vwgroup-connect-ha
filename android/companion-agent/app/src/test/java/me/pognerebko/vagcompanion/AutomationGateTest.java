package me.pognerebko.vagcompanion;

import static org.junit.Assert.*;
import org.junit.Test;

public final class AutomationGateTest {
    @Test public void pauseBlocksAllActionsAndResumeDoesNotReplayOldOnes() {
        AutomationGate gate = new AutomationGate();
        long old = gate.lease();
        assertTrue(gate.allows(old));
        gate.setPaused(true);
        assertTrue(gate.isSuspended());
        assertFalse(gate.allows(old));
        assertEquals(-1, gate.lease());
        gate.setPaused(false);
        assertFalse(gate.allows(old));
        assertTrue(gate.allows(gate.lease()));
    }

    @Test public void controlsScreenCannotBeStolenAndDoesNotClearManualPause() {
        AutomationGate gate = new AutomationGate();
        long old = gate.lease();
        gate.setControlsVisible(true);
        assertTrue(gate.isSuspended());
        assertFalse(gate.allows(old));
        gate.setPaused(true);
        gate.setControlsVisible(false);
        assertTrue(gate.isSuspended());
        gate.setPaused(false);
        assertFalse(gate.isSuspended());
        assertFalse(gate.allows(old));
    }

    @Test public void enablingWhileControlsOpenWaitsForLeavingScreen() {
        AutomationGate gate = new AutomationGate();
        gate.setPaused(true);
        gate.setControlsVisible(true);
        gate.setPaused(false);
        assertEquals(-1, gate.lease());
        gate.setControlsVisible(false);
        assertTrue(gate.allows(gate.lease()));
    }
}
