package me.pognerebko.vagcompanion;

import android.graphics.Rect;
import android.view.accessibility.AccessibilityNodeInfo;

import java.util.Locale;

final class UiHierarchySerializer {
    private static final int MAX_DEPTH = 64;
    private static final int MAX_NODES = 5000;

    private UiHierarchySerializer() {}

    static String serialize(AccessibilityNodeInfo root) {
        StringBuilder xml = new StringBuilder(32_768);
        xml.append("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>")
                .append("<hierarchy rotation=\"0\">");
        Counter counter = new Counter();
        appendNode(xml, root, 0, 0, counter);
        xml.append("</hierarchy>");
        return xml.toString();
    }

    private static void appendNode(
            StringBuilder xml,
            AccessibilityNodeInfo node,
            int index,
            int depth,
            Counter counter) {
        if (node == null || depth > MAX_DEPTH || counter.value >= MAX_NODES) {
            return;
        }
        counter.value++;

        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);
        xml.append("<node")
                .append(attr("index", Integer.toString(index)))
                .append(attr("text", chars(node.getText())))
                .append(attr("resource-id", chars(node.getViewIdResourceName())))
                .append(attr("class", chars(node.getClassName())))
                .append(attr("package", chars(node.getPackageName())))
                .append(attr("content-desc", chars(node.getContentDescription())))
                .append(attr("checkable", bool(node.isCheckable())))
                .append(attr("checked", bool(node.isChecked())))
                .append(attr("clickable", bool(node.isClickable())))
                .append(attr("enabled", bool(node.isEnabled())))
                .append(attr("focusable", bool(node.isFocusable())))
                .append(attr("focused", bool(node.isFocused())))
                .append(attr("scrollable", bool(node.isScrollable())))
                .append(attr("long-clickable", bool(node.isLongClickable())))
                .append(attr("password", bool(node.isPassword())))
                .append(attr("selected", bool(node.isSelected())))
                .append(attr("bounds", String.format(
                        Locale.ROOT,
                        "[%d,%d][%d,%d]",
                        bounds.left,
                        bounds.top,
                        bounds.right,
                        bounds.bottom)))
                .append(">");

        int childCount = node.getChildCount();
        for (int childIndex = 0; childIndex < childCount; childIndex++) {
            AccessibilityNodeInfo child = node.getChild(childIndex);
            if (child == null) {
                continue;
            }
            try {
                appendNode(xml, child, childIndex, depth + 1, counter);
            } finally {
                child.recycle();
            }
        }
        xml.append("</node>");
    }

    private static String attr(String name, String value) {
        return " " + name + "=\"" + escape(value) + "\"";
    }

    private static String chars(CharSequence value) {
        return value == null ? "" : value.toString();
    }

    private static String bool(boolean value) {
        return value ? "true" : "false";
    }

    static String escape(String value) {
        StringBuilder out = new StringBuilder(value.length());
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '&': out.append("&amp;"); break;
                case '<': out.append("&lt;"); break;
                case '>': out.append("&gt;"); break;
                case '\"': out.append("&quot;"); break;
                case '\'': out.append("&apos;"); break;
                default:
                    if (c == '\n' || c == '\r' || c == '\t' || c >= 0x20) {
                        out.append(c);
                    }
            }
        }
        return out.toString();
    }

    private static final class Counter {
        int value;
    }
}
