package me.pognerebko.vagcompanion;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class UiHierarchySerializerTest {
    @Test
    public void escapesXmlAttributes() {
        assertEquals(
                "Battery &amp; &lt;80%&gt; &quot;ready&quot; &apos;now&apos;",
                UiHierarchySerializer.escape("Battery & <80%> \"ready\" 'now'"));
    }

    @Test
    public void dropsInvalidXmlControlCharacters() {
        assertEquals("a\tb\nc", UiHierarchySerializer.escape("a\u0001\tb\nc"));
    }
}
