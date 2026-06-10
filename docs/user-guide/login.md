# Logging In

Lightfall uses role-based access control to manage permissions. This guide explains how to log in and what access levels are available.

## Authentication Methods

Lightfall supports multiple authentication methods depending on your environment:

### Keycloak (Production)

For production use at the ALS facility, Lightfall uses Keycloak single sign-on:

1. Launch Lightfall - the login dialog appears automatically
2. Click **Sign in with Browser**
3. Your web browser opens to the ALS authentication page
4. Enter your ALS credentials
5. After successful authentication, the browser redirects back and Lightfall completes login

Your session remains active for 8 hours (configurable in Preferences).

### Local Authentication (Development)

For development or testing environments:

1. In the login dialog, click **Use local account instead**
2. Enter your local username and password
3. Click **Sign In**

### Guest Access

For read-only access without authentication:

1. In the login dialog, click **Continue as Guest**
2. You'll have limited access to view data but cannot control devices or run plans

## User Roles

Lightfall assigns roles that determine your permissions:

| Role | Description | Capabilities |
|------|-------------|--------------|
| **Guest** | Unauthenticated access | View-only access to data and logs |
| **User** | Standard user | Run plans, control allowed devices |
| **Operator** | Equipment operator | Extended device control |
| **Beamline Scientist** | BL staff | Full beamline access, custom configurations |
| **Staff** | ALS staff | Administrative functions |
| **Developer** | Development access | Debug features, development tools |

Your current role is displayed in the status bar at the bottom of the window.

## Session Management

### Session Timeout

Your session automatically expires after a configurable period (default: 8 hours). When your session is about to expire:

1. A notification appears warning of pending expiration
2. The status bar shows remaining session time
3. When expired, the login dialog reappears

To adjust session duration, go to **Preferences** > **Login & Session**.

### Session Status

The status bar shows your authentication state:

- **Green indicator**: Authenticated and connected
- **Yellow indicator**: Session expiring soon
- **Red indicator**: Not authenticated or session expired

### Logging Out

To log out manually:

1. Go to **File** > **Log Out**
2. Or close the application

Your session state is not preserved between application restarts for security.

## Troubleshooting

### Browser login doesn't complete

If the browser authentication succeeds but Lightfall doesn't complete login:

1. Check that your browser allowed the redirect
2. Ensure no popup blockers are interfering
3. Try clicking **Sign in with Browser** again

### Session expires unexpectedly

If your session expires before the configured timeout:

1. Check your network connection to the authentication server
2. Verify the server hasn't been restarted
3. Contact your system administrator if the issue persists

### Guest mode limitations

In guest mode, you cannot:

- Run data acquisition plans
- Control hardware devices
- Modify logbook entries
- Access certain panels

Sign in with your credentials for full access.
