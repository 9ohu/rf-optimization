"""Customer-complaint RF analysis (geometry + optional KPI).

    from rfopt.complaints import load_complaints, analyze_complaints, audit_table
    comp = load_complaints("tickets.xlsx")
    res  = analyze_complaints(comp, site_db)          # per-ticket findings
    audit_table(res)                                  # tool verdict vs engineer
"""

from rfopt.complaints.loader import load_complaints, ComplaintColumns
from rfopt.complaints.analyze import (analyze_complaints, ComplaintFinding,
                                      ComplaintResult, audit_table,
                                      aggregate_by_site, aggregate_by_subdistrict)
from rfopt.complaints.site_audit import (audit_site, audit_sites, SiteAudit,
                                         Flag)
from rfopt.complaints.worklist import (load_worklist, process_worklist,
                                       write_worklist_report, run_worklist,
                                       TicketResult)

__all__ = ["load_complaints", "ComplaintColumns", "analyze_complaints",
           "ComplaintFinding", "ComplaintResult", "audit_table",
           "aggregate_by_site", "aggregate_by_subdistrict",
           "audit_site", "audit_sites", "SiteAudit", "Flag",
           "load_worklist", "process_worklist", "write_worklist_report",
           "run_worklist", "TicketResult"]
