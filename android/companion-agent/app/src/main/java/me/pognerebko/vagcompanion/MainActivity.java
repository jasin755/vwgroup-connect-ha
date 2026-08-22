package me.pognerebko.vagcompanion;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.provider.Settings;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

public final class MainActivity extends Activity {
    private TextView status;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        int pad = dp(24);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(pad, pad, pad, pad);
        root.setBackgroundColor(Color.rgb(16, 16, 16));

        TextView title = new TextView(this);
        title.setText(R.string.app_name);
        title.setTextColor(Color.WHITE);
        title.setTextSize(26);
        root.addView(title, matchWrap(0, dp(18)));

        status = new TextView(this);
        status.setTextColor(Color.LTGRAY);
        status.setTextSize(17);
        root.addView(status, matchWrap(0, dp(24)));

        Button settings = new Button(this);
        settings.setText(R.string.open_settings);
        settings.setOnClickListener(view -> startActivity(
                new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)));
        root.addView(settings, matchWrap(0, dp(12)));

        Button volkswagen = new Button(this);
        volkswagen.setText(R.string.open_vw);
        volkswagen.setOnClickListener(view -> {
            Intent launch = getPackageManager().getLaunchIntentForPackage(
                    VagAccessibilityService.VOLKSWAGEN_PACKAGE);
            if (launch != null) {
                startActivity(launch);
            }
        });
        root.addView(volkswagen, matchWrap(0, dp(24)));

        TextView note = new TextView(this);
        note.setText(R.string.privacy_note);
        note.setTextColor(Color.GRAY);
        note.setTextSize(14);
        root.addView(note, matchWrap(0, 0));

        setContentView(root);
    }

    @Override
    protected void onResume() {
        super.onResume();
        boolean configured = isServiceConfigured();
        boolean connected = VagAccessibilityService.getInstance() != null;
        status.setText(configured && connected
                ? R.string.status_enabled
                : R.string.status_disabled);
        status.setTextColor(configured && connected
                ? Color.rgb(76, 217, 100)
                : Color.rgb(255, 159, 10));
    }

    private boolean isServiceConfigured() {
        String enabled = Settings.Secure.getString(
                getContentResolver(),
                Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
        if (enabled == null) {
            return false;
        }
        String expected = new ComponentName(this, VagAccessibilityService.class)
                .flattenToString();
        for (String component : enabled.split(":")) {
            if (expected.equalsIgnoreCase(component)) {
                return true;
            }
        }
        return false;
    }

    private LinearLayout.LayoutParams matchWrap(int top, int bottom) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, top, 0, bottom);
        return params;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
