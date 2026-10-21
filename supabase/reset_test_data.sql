-- FromTheLogo — wipe test data so real play starts clean.
-- Run AFTER supabase/schema.sql. Deletes every daily pack, pick, finished score and
-- pending guest transfer. Accounts and usernames are kept.
truncate table picks, submissions, transfer_tokens, daily_packs restart identity;

-- Optional: also delete every account and guest (their usernames go with them).
-- Only uncomment this if all current accounts are your own test accounts.
-- delete from auth.users;
