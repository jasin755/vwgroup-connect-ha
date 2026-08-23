package me.pognerebko.vagcompanion;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.security.SecureRandom;

public final class MainActivity extends Activity {
    private static final char[] HEX = "0123456789abcdef".toCharArray();

    private TextView status;
    private TextView configurationStatus;
    private EditText relayUrl;
    private EditText token;

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
        root.addView(status, matchWrap(0, dp(14)));

        configurationStatus = new TextView(this);
        configurationStatus.setTextColor(Color.LTGRAY);
        configurationStatus.setTextSize(15);
        root.addView(configurationStatus, matchWrap(0, dp(18)));

        relayUrl = new EditText(this);
        relayUrl.setHint(R.string.relay_url_hint);
        relayUrl.setText(AgentRelayClient.loadRelayUrl(this));
        relayUrl.setSingleLine(true);
        relayUrl.setTextColor(Color.WHITE);
        relayUrl.setHintTextColor(Color.GRAY);
        root.addView(relayUrl, matchWrap(0, dp(10)));

        token = new EditText(this);
        token.setHint(R.string.token_hint);
        token.setText(AgentHttpServer.loadToken(this));
        token.setSingleLine(true);
        token.setInputType(
                InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD);
        token.setTextColor(Color.WHITE);
        token.setHintTextColor(Color.GRAY);
        root.addView(token, matchWrap(0, dp(10)));

        Button generate = new Button(this);
        generate.setText(R.string.generate_token);
        generate.setOnClickListener(view -> token.setText(randomToken()));
        root.addView(generate, matchWrap(0, dp(8)));

        Button copy = new Button(this);
        copy.setText(R.string.copy_token);
        copy.setOnClickListener(view -> copyToken());
        root.addView(copy, matchWrap(0, dp(8)));

        Button save = new Button(this);
        save.setText(R.string.save_configuration);
        save.setOnClickListener(view -> saveConfiguration());
        root.addView(save, matchWrap(0, dp(18)));

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

        ScrollView scroll = new ScrollView(this);
        scroll.addView(root);
        setContentView(scroll);
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
        updateConfigurationStatus();
    }

    private void saveConfiguration() {
        String url = relayUrl.getText().toString().trim();
        String secret = token.getText().toString().trim();
        if ((!url.startsWith("https://") && !url.startsWith("http://"))
                || !AgentHttpServer.isValidToken(secret)) {
            configurationStatus.setText(R.string.configuration_invalid);
            configurationStatus.setTextColor(Color.rgb(255, 69, 58));
            return;
        }
        AgentHttpServer.saveToken(this, secret);
        // Empty channel selects HA's token-discovery endpoint. HA binds this
        // random secret to the one companion config entry that carries it.
        AgentRelayClient.saveConfig(this, url, "");
        VagAccessibilityService service = VagAccessibilityService.getInstance();
        if (service != null) {
            service.restartRelayClient();
        }
        updateConfigurationStatus();
    }

    private void copyToken() {
        String secret = token.getText().toString().trim();
        if (!AgentHttpServer.isValidToken(secret)) {
            configurationStatus.setText(R.string.configuration_invalid);
            configurationStatus.setTextColor(Color.rgb(255, 69, 58));
            return;
        }
        ClipboardManager clipboard =
                (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        if (clipboard != null) {
            clipboard.setPrimaryClip(ClipData.newPlainText("Agent token", secret));
            Toast.makeText(this, R.string.token_copied, Toast.LENGTH_SHORT).show();
        }
    }

    private void updateConfigurationStatus() {
        boolean ready = !AgentRelayClient.loadRelayUrl(this).isEmpty()
                && AgentHttpServer.isValidToken(AgentHttpServer.loadToken(this));
        configurationStatus.setText(
                ready ? R.string.configuration_saved : R.string.configuration_missing);
        configurationStatus.setTextColor(
                ready ? Color.rgb(76, 217, 100) : Color.rgb(255, 159, 10));
    }

    private static String randomToken() {
        byte[] bytes = new byte[32];
        new SecureRandom().nextBytes(bytes);
        StringBuilder out = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) {
            int unsigned = value & 0xff;
            out.append(HEX[unsigned >>> 4]);
            out.append(HEX[unsigned & 0x0f]);
        }
        return out.toString();
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
