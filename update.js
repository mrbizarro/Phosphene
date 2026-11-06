// Pinokio reads this before the source moves. Keep the transaction in one
// process: durable source receipt, dependency repair, then a pending restart.
// Old launcher versions still follow their old path on the first update.
module.exports = {
  run: [{
    method: "shell.run",
    params: {
      message: "bash scripts/pinokio/update_transaction.sh || printf 'FATAL \\145rror: Update did not finish. Run Update again; keep local edits.\\n'"
    }
  }]
}
