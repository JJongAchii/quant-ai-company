# Initial-case citation follow-up

`RESULT.json` is the bounded, secret-free evidence summary. Original model responses, frozen
source excerpts, the new critique response and the durable request/release receipts remain in
the production host's private `quant-feed-quality-20260928` operation directory. They are not
copied to Git. The first unrooted archive failure and the intentionally interrupted partial
image build are retained there rather than erased or reported as a completed release.

`qualify_saved_revision.py` and `run_staged_saved_revision.py` are historical one-off drivers.
They can invoke the real ChatGPT-subscription runtime; they must not be rerun after a returned
or uncertain call without reconciling the existing private receipt and current service state.
The saved revision received an independent `revise`, not `pass`. No Slack publication or database
document write was made by these scripts, and neither script authorizes a production cutover.
