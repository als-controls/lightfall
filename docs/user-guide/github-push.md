# Pushing to GitHub

If you work on Lightfall's source checkouts, you can push your commits to
GitHub from inside the application. You can do it yourself from the git-state
dialog, or let the assistant do it for you, and in both cases nothing is sent
until you have signed in. This page covers signing in, who can push, and what
the assistant is allowed to do.

## Who can push

Pushing is available to the same people who see the update and git-state
indicators: you must be **logged in**, **online**, and hold the `ADMIN_SYSTEM`
permission (today: ADMIN and DEVELOPER). See
[Updates and Source Checkouts](updates.md). Without that, the Push buttons are
disabled and the assistant refuses.

Only repositories whose remote is on `github.com` can be pushed. A checkout
with no GitHub remote, a detached HEAD, or nothing to push is refused with the
reason.

## Signing in

Open **Preferences → GitHub** (or press **Push** in the git-state dialog while
signed out) and choose **Sign in with GitHub**.

Signing in lasts for the current session only. The GitHub credential is kept
in memory, is never written to disk, and is dropped when you log out of
Lightfall or a different user logs in. Only the name of your GitHub account is
remembered, so that Lightfall can warn you if you sign in as someone else.

### Signing in with a code (the Lightfall GitHub App)

1. Press **Sign in with GitHub**. Lightfall shows a short code.
2. Press **Open github.com/login/device** (or copy the code yourself), enter the
   code on GitHub and authorize Lightfall.
3. Lightfall notices the approval and the dialog closes.

This sign-in lasts about 8 hours. When it expires you are shown as signed out
and simply sign in again.

The Lightfall GitHub App must be **installed on the repository** you want to
push to, and your own account needs write access to it. If GitHub refuses a
push with "permission denied" or "repository not found", the app is probably
not installed there. Ask the repository owner to install it, or use a token
instead.

### Signing in with a fine-grained token

If the app can't be installed, press **Use a token** and paste a fine-grained
personal access token. Only fine-grained tokens (they start with
`github_pat_`) are accepted. Create one on GitHub under *Settings → Developer
settings → Personal access tokens → Fine-grained tokens* with:

- **Repository access**: the repositories you want to push to.
- **Contents**: Read and write.
- **Workflows**: Read and write, only if you push changes to
  `.github/workflows/` files.

The token is held in memory for the session, like the code sign-in, and stops
working at its own expiry date.

## What a push does

A push sends the **current branch** to the branch of the same name on its
GitHub remote. It never forces, never deletes, and never pushes other branches
or tags. If the remote branch has commits you don't have, the push is rejected
and you are told to pull first. Git hooks are not run, and the push is refused
if your git configuration would redirect it to another URL or turn off
certificate checking.

## The assistant and pushing

The assistant has two tools for this:

- **List repositories** shows each source checkout, its branch, whether it has
  uncommitted changes, whether it can be pushed, and whether you are signed in
  to GitHub. It changes nothing.
- **Push** pushes one checkout, given by name or by path.

The assistant never sees your token. It also never pushes without asking:
**every push** shows you a question in the chat, such as *Push my-branch of
/path/to/repo to https://github.com/owner/repo.git as your-login?*, with only
**Allow** and **Deny**. Allow pushes once; there is no "always allow". Deny, or
closing the question without an answer, pushes nothing and the assistant is
told so.

If you are not signed in, the assistant doesn't ask. It tells you to sign in
and try again.
