# Logging In

Lightfall uses role-based access control: what you can see and do depends on
who you are signed in as. The login dialog appears automatically at startup
(and again if your session expires).

> 🖼️ **Image placeholder** — *Screenshot: the login dialog showing the Keycloak, Linux User Login, and Continue as Guest buttons*

## Authentication methods

### Keycloak (production)

At a deployed beamline, Lightfall authenticates against the facility's
Keycloak single sign-on:

1. Click **Login with Keycloak**.
2. A browser opens to the facility authentication page (Lightfall uses an
   embedded browser window when available, falling back to your system
   browser).
3. Enter your facility credentials.
4. The browser redirects back and Lightfall completes the login.

While the browser flow is in progress the dialog shows a
*"Waiting for browser login..."* indicator with a **Cancel** button.

### Linux user login (PAM)

On Linux workstations, **Linux User Login** authenticates with your local
operating-system account via PAM. This button is hidden on Windows.

### Local accounts (development)

For development and demo environments without Keycloak:

1. Click the **Use local account instead** link at the bottom of the dialog.
2. Enter a username and password and click **Login**.

The local provider ships with built-in development accounts, one per role:
`admin`/`admin`, `developer`/`developer`, `staff`/`staff`,
`operator`/`operator`, `user`/`user`, and `guest`/`guest`. Additional local
users can be defined in a YAML users file. **Back to Keycloak login** returns
to the main view.

### Guest access

**Continue as Guest** opens the application without authenticating. Guest
access is read-only: you can view panels, devices, and data, but you cannot
control devices or run plans, and your logbook notes stay on the local
machine (they are not synced to a logbook server).

## User roles

Roles are hierarchical — each role includes the permissions of the ones below
it:

| Role | Typical user | Capabilities |
|------|--------------|--------------|
| **Guest** | Unauthenticated | View-only access |
| **User** | Experimenter | Run plans, control allowed devices |
| **Operator** | Equipment operator | Extended device control |
| **Staff** | Facility staff | Staff-level operations |
| **Admin** | Administrator | Administrative functions |
| **Developer** | Developer | Debug features, development tools |

Some panels and actions are only available above a given role; panels you
lack permission for simply do not appear in the sidebar or the **View →
Panels** menu.

## Sessions

### Duration

For local accounts, the session duration is configurable in **File → Settings
→ Login & Session** (15 minutes to 8 hours; the default is 2 hours). Keycloak
session lifetimes are controlled by the server.

When a session expires, the login dialog reappears so you can sign in again
or continue as a guest.

### Service keys

At login, Lightfall mints short-lived per-service API keys for the Tiled data
catalog and the logbook server on your behalf. This is automatic; if a
service is unreachable at login time you may later see unauthenticated (401)
errors for that one service — logging out and back in re-mints the keys.

### Offline mode

If the authentication service becomes unreachable, Lightfall drops into
offline mode: only view operations are permitted, and the application retries
the connection every 30 seconds. When the service comes back, your previous
authentication state is restored — though if your service keys expired in the
meantime, you will need to log in again.

### Logging out

Use **User → Logout** in the menu bar. Logging out clears your session and
service keys and purges already-synced logbook data from the local cache.
Sessions are not preserved across application restarts.

## Troubleshooting

### Browser login doesn't complete

1. Check that the browser actually reached the authentication page (network,
   VPN, proxy).
2. Make sure nothing blocked the redirect back to Lightfall.
3. Click **Cancel** and try **Login with Keycloak** again.

### Session expires earlier than expected

1. Check connectivity to the authentication server.
2. For Keycloak logins, remember the server's session policy wins over the
   local duration setting.

### Guest mode limitations

In guest mode you cannot run plans, control devices, or sync logbook entries.
Sign in with a real account for full access.
