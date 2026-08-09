# Publishing the Wiki

The files under `wiki/` in the main repository are the reviewed source for the
GitHub Wiki. Changes should begin in a normal branch and pull request rather
than only in the GitHub Wiki editor.

The repository's `README.md`, `docs/`, `CHANGELOG.md`, and `ROADMAP.md` remain
authoritative. Wiki pages should organize tasks, summarize common workflows,
and link to canonical documents instead of copying large technical sections.

## Review workflow

1. Create a documentation branch from `main`.
2. Edit the Markdown files under `wiki/`.
3. Run the documentation and whitespace checks.
4. Review the rendered pages in the pull request.
5. Merge the source changes before publishing them to the wiki repository.

```bash
python scripts/check_docs.py
git diff --check
```

## Initializing the GitHub Wiki

The GitHub Wiki must be enabled and initialized before its separate Git
repository can be cloned. Create the first page through the repository's Wiki
tab when necessary.

Clone the wiki repository into a temporary checkout:

```bash
WIKI_CHECKOUT=/tmp/sds200-python-wiki

rm -rf "$WIKI_CHECKOUT"

git clone \
  git@github.com:stevenboyd78/sds200-python.wiki.git \
  "$WIKI_CHECKOUT"
```

Determine the branch checked out by the clone:

```bash
WIKI_BRANCH="$(
  git -C "$WIKI_CHECKOUT" symbolic-ref --quiet --short HEAD
)"

if [ -z "$WIKI_BRANCH" ]; then
  echo "Could not determine the wiki branch." >&2
  exit 1
fi

printf 'Wiki branch: %s\n' "$WIKI_BRANCH"
```

Cloning checks out the branch GitHub currently uses for the wiki. Do not assume
that its name is `master` or `main`.

## Publish reviewed pages

Run these commands from the main repository checkout after the source pull
request has been merged.

Remove previously published Markdown pages from the temporary wiki checkout,
then copy every reviewed source page from `wiki/`:

```bash
find "$WIKI_CHECKOUT" \
  -maxdepth 1 \
  -type f \
  -name '*.md' \
  -delete

find wiki \
  -maxdepth 1 \
  -type f \
  -name '*.md' \
  -exec install -m 0644 {} "$WIKI_CHECKOUT/" \;
```

This makes the published wiki match the reviewed source directory, including
page removals and renames.

Inspect the pending wiki change:

```bash
git -C "$WIKI_CHECKOUT" status --short
git -C "$WIKI_CHECKOUT" diff --check
git -C "$WIKI_CHECKOUT" diff
```

Stage all Markdown additions, modifications, and deletions:

```bash
git -C "$WIKI_CHECKOUT" add -A -- '*.md'
git -C "$WIKI_CHECKOUT" status --short
git -C "$WIKI_CHECKOUT" diff --cached --check
git -C "$WIKI_CHECKOUT" diff --cached
```

Do not create an empty publication commit:

```bash
if git -C "$WIKI_CHECKOUT" diff --cached --quiet; then
  echo "Wiki is already synchronized."
else
  git -C "$WIKI_CHECKOUT" commit -m "Publish reviewed wiki source"
  git -C "$WIKI_CHECKOUT" push origin "$WIKI_BRANCH"
fi
```

## Verify publication

After pushing, confirm the local wiki checkout is clean:

```bash
git -C "$WIKI_CHECKOUT" status --short
git -C "$WIKI_CHECKOUT" log -1 --oneline --decorate
```

Open the repository's Wiki tab and verify:

- `Home` is the landing page;
- the sidebar appears;
- internal page links resolve;
- canonical repository links open the intended default-branch documents;
- removed source pages are no longer published.

## Release synchronization

When a release changes wiki source, publish the reviewed wiki after the
release-preparation pull request is merged and before the release tag is
created. This keeps the public task-oriented guidance synchronized with the
release commit before package and Home Assistant App publication begins.

Do not publish release-branch-only wiki content before it has merged into
`main`.

## Preventing drift

- Treat direct wiki-editor changes as emergency edits.
- Backport any direct wiki edit into `wiki/` immediately.
- Keep commands and support claims aligned with the default branch.
- Link to canonical repository documents for detailed or release-sensitive
  behavior.
- Update wiki source in the same pull request when a user-facing workflow
  changes materially.
- Publish only content already merged into the main repository.
