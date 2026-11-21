# Recipient receipts over SMTP

This test uses Python's SMTP client and aiosmtpd 1.4.6 on the loopback interface. No message leaves the computer, and no model is used.

`protocol.json` fixes ten transport conditions, repeated for each of the 50 original request grants. `saved/` contains those 500 transactions. The conditions cover ordinary sends, an unauthorized To/Cc/Bcc destination, an explicit envelope that differs from the headers, partial recipient rejection, rejection of the whole message, and a lost final acknowledgment.

The client-side adapter builds its receipt from the grant, recipient replies, and final DATA reply. It cannot read the server's stored record. That separate record is the scoring source. The adapter issues 450 correct definitive judgments and leaves 50 outcomes unknown.

After that run, we added a paired uncertainty control, documented in `unknown-control-protocol.json`. Its 50 transactions close the connection before storage, rather than after storage. The two conditions have the same client-observed recipient replies, missing final acknowledgment, and public receipt. The server outcomes differ. Both receipts remain unknown. The full set therefore contains 550 transactions and 100 abstentions.

These are eleven constructed protocol conditions, not 550 independent tasks or examples of spontaneous agent mistakes. Complete logs containing the grant, SMTP envelope and replies would support the same decisions. The experiment checks whether a restricted receipt retains the right evidence. It does not introduce new SMTP semantics, prove final delivery, or cover concurrent transactions or a production mail provider.

The script's `--verify` command checks saved records. `--run --verify` reruns the initial conditions on a temporary local server. Add `--unknown-control --output smtp-study/rerun-unknown` to rerun the paired control. Use a separate output directory to retain the original records.
