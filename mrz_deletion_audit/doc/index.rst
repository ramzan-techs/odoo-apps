====================
Deletion Audit Trail
====================

A record disappears and nobody knows what happened. Standard Odoo keeps no
trace of a deleted record: no name, no data, no author. Deletion Audit Trail
keeps a permanent, read-only log of every deletion, with a full snapshot of
what the record contained, including the lines and child records that went
with it.

Getting started
===============

Install the app. The main business documents are tracked from the start:
contacts, products, quotations, invoices, transfers, projects, tasks and
employees, among others. Delete one and the log is there.

Everything lives under the **Deletion Audit** menu:

* **Deleted Records** - every deletion, most recent first
* **Analysis** - the same data in pivot and graph views
* **Configuration > Tracking Rules** - which documents are tracked
* **Configuration > Settings** - what to track and how long to keep it
* **User Guide** - the illustrated version of this document

Who can see what
================

Two groups come with the app:

Auditor
    Reads the logs of the documents they are already allowed to read. Someone
    who cannot open invoices cannot read the log of a deleted invoice either,
    so the app never becomes a way around your access rights.

Administrator
    Reads every log and manages the tracking rules and the settings.

Nobody can edit or delete a log from the interface, administrators included.
Logs leave only through the retention policy.

Reading a deletion log
======================

Open a log from **Deleted Records**. The top of the form answers two
questions.

**Deleted Record** says what disappeared: the document type, the technical
model, and the ID the record had.

**Deleted By** says who removed it and how: the user, the date and time, the
origin, the IP address and the browser.

Below that:

* **Deleted Data** lists every captured field with a readable value:
  names instead of IDs, labels instead of codes.
* **Cascaded Records** lists what the database removed along with the record,
  such as order lines or invoice lines.
* **Technical** holds the user agent, the request path and the transaction
  reference.

Two buttons appear when there is something to show: **Cascaded**, for the
records deleted along with this one, and **Same Transaction**, for everything
removed in the same operation.

Where a deletion came from
==========================

Every log records its origin:

===================  ==========================================================
Origin               What it means
===================  ==========================================================
User Interface       Someone clicked Delete in Odoo
External API         An integration or script connected through XML-RPC or
                     JSON-RPC
Website / Portal     A public or portal page
Scheduled Action     A cron job, for example a data cleaning rule
Cascade              The database removed it together with its parent record
System / Script      Odoo itself, a module installation, or a shell command
===================  ==========================================================

Finding a deleted record
========================

The search bar looks inside the captured data, so you can find a record by
anything it contained: an email address, a reference, an amount, a product
name. Searching for a customer's email finds the deleted contact that held it.

Ready-made filters cover the common questions: **Direct Deletions**,
**Cascaded**, **Deleted by Me**, **User Interface**, **External API**,
**Scheduled Actions** and **Deletion Date**. Group by **Document Type**,
**Deleted By**, **Origin**, **Deletion Date** or **Company**.

**Analysis** opens the same records as a pivot table or a graph, to compare
periods, users and document types.

Choosing what to track
======================

**Configuration > Tracking Rules** holds one rule per document type:

* **Mode** - track this document, or ignore it
* **Capture Cascaded Records** - also keep what the database removes with it
* **Excluded Fields** - fields to leave out of the snapshot

Technical models are skipped automatically, so the log stays about your
business documents.

Settings and retention
======================

**Configuration > Settings**:

==========================  ===================================================
Setting                     What it does
==========================  ===================================================
Track All Business Models   Log every business model, not only the ones listed
                            in the tracking rules
Cascaded Records            The largest number of child records captured for a
                            single deletion
Keep Logs For               Logs older than this many days are purged every
                            night. Set 0 to keep them forever.
==========================  ===================================================

What is captured, and what is not
=================================

Captured:

* every stored field of the record, as it was at the moment of the deletion
* the records the database removed with it, linked to their parent
* the names and sizes of the files that were attached to it
* who deleted it, when, from where, with the IP address and browser

Never stored:

* passwords, API keys, tokens and other secrets
* fields you listed as excluded on the tracking rule
* computed values that Odoo does not keep in the database

Not logged
==========

Some things look like deletions but are not:

* **Archiving.** An archived record still exists, so nothing is logged.
* **Failed deletions.** The log is written in the same transaction as the
  deletion. If the deletion fails or is rolled back, no log is left behind.
* **Untracked documents**, and technical models.

Troubleshooting
===============

**A deletion was not logged.** Check the tracking rule of that document type
in **Configuration > Tracking Rules**, or turn on **Track All Business
Models**. Archived records and failed deletions are never logged.

**Logs disappeared.** **Keep Logs For** purges logs older than the number of
days you set. Set it to 0 to keep them forever.

**The IP address is always the same.** Behind a reverse proxy, Odoo sees the
proxy. Set ``proxy_mode = True`` in the Odoo configuration file and make the
proxy send the ``X-Forwarded-For`` header.

**A deletion shows no browser.** Deletions made by scheduled actions,
integrations and scripts have no browser to report.

Support
=======

Questions, bug reports and feature requests are welcome at
techsramzan@gmail.com.
