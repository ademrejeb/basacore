import { KeyRound } from "lucide-react";

import { Checkbox, FormField, Input, Select, Textarea } from "@adem.rejeb/ui";

import {
  createApplicationClientAction,
  createCredentialAction,
} from "@/app/actions";
import { ActionDialog } from "@/components/action-dialog";

const CLIENT_SCOPES = ["openid", "profile", "email", "offline_access"];

const API_SCOPES = [
  "app:config.read",
  "app:tenant.resolve",
  "app:identity.read",
  "app:authorization.check",
  "app:usage.write",
  "app:audit.write",
];

export function CreateLoginClientDialog({
  applicationId,
  triggerVariant = "primary",
}: {
  applicationId: string;
  triggerVariant?: "primary" | "secondary" | "outline";
}) {
  return (
    <ActionDialog
      label="Set up sign-in"
      triggerVariant={triggerVariant}
      icon={<KeyRound size={15} />}
      title="Set up sign-in"
      description="A sign-in client is the credential your application uses to start sign-in. Redirect URIs must match exactly."
      action={createApplicationClientAction}
      submitLabel="Create sign-in client"
      closeOnSuccess={false}
      size="lg"
    >
      <input type="hidden" name="id" value={applicationId} />
      <FormField label="Name" required>
        <Input name="name" placeholder="Customer web app" required />
      </FormField>
      <div className="form-row">
        <FormField label="Client type">
          <Select name="client_type" defaultValue="confidential">
            <option value="confidential">Confidential (server-side)</option>
            <option value="public">Public (SPA / native, PKCE only)</option>
          </Select>
        </FormField>
        <FormField label="PKCE">
          <Checkbox name="pkce_required" label="Require S256 PKCE" defaultChecked />
        </FormField>
      </div>
      <FormField label="Redirect URIs" description="One per line, exact match required." required>
        <Textarea
          name="redirect_uris"
          rows={3}
          placeholder="https://app.example.com/auth/callback"
          required
        />
      </FormField>
      <FormField label="Post-logout redirect URIs" description="One per line.">
        <Textarea name="post_logout_redirect_uris" rows={2} placeholder="https://app.example.com/" />
      </FormField>
      <FormField label="Allowed browser origins" description="One per line, optional.">
        <Textarea name="allowed_origins" rows={2} placeholder="https://app.example.com" />
      </FormField>
      <FormField label="Scopes" description="openid is required; offline_access enables refresh tokens.">
        <div className="stack" style={{ gap: 8 }}>
          {CLIENT_SCOPES.map((scope) => (
            <Checkbox
              key={scope}
              name="scopes"
              value={scope}
              defaultChecked={scope !== "offline_access"}
              label={<span className="mono">{scope}</span>}
            />
          ))}
        </div>
      </FormField>
    </ActionDialog>
  );
}

export function CreateCredentialDialog({
  applicationId,
  triggerVariant = "primary",
}: {
  applicationId: string;
  triggerVariant?: "primary" | "secondary" | "outline";
}) {
  return (
    <ActionDialog
      label="Create API credential"
      triggerVariant={triggerVariant}
      icon={<KeyRound size={15} />}
      title="Create API credential"
      description="Used by the application backend for server-to-server calls. Select only the scopes this integration needs; the secret is shown once."
      action={createCredentialAction}
      submitLabel="Create credential"
      closeOnSuccess={false}
      size="lg"
    >
      <input type="hidden" name="id" value={applicationId} />
      <FormField label="Credential name" required>
        <Input name="name" placeholder="Production backend" required />
      </FormField>
      <FormField label="Scopes" required>
        <div className="stack" style={{ gap: 8 }}>
          {API_SCOPES.map((scope) => (
            <Checkbox
              key={scope}
              name="scopes"
              value={scope}
              defaultChecked={
                scope === "app:config.read" ||
                scope === "app:tenant.resolve" ||
                scope === "app:identity.read"
              }
              label={<span className="mono">{scope}</span>}
            />
          ))}
        </div>
      </FormField>
      <FormField label="Expires in days" description="Leave blank for no expiry.">
        <Input name="expires_in_days" type="number" min={1} max={730} placeholder="90" />
      </FormField>
    </ActionDialog>
  );
}
