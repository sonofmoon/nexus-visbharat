#!/usr/bin/env python3
"""
Generate an executive 12-slide landscape presentation PDF for Nexus VisBharat.
Compatible with Hack2skill submission (<5MB PDF requirement).
Features jury-winning metrics, Google Cloud tech stack, DPDP Act 2023 compliance,
and 108-case multilingual benchmark.
"""
import sys
from pathlib import Path

from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas


class PresentationCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_slide_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_slide_decorations(self, page_count):
        self.saveState()

        # Top Accent Stripe (Google Blue)
        self.setFillColor(colors.HexColor("#1a73e8"))
        self.rect(0, 606, 792, 6, fill=True, stroke=False)

        # Bottom Bar & Metadata
        self.setStrokeColor(colors.HexColor("#e2e8f0"))
        self.setLineWidth(1)
        self.line(36, 38, 756, 38)

        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(colors.HexColor("#64748b"))
        self.drawString(36, 24, "Nexus VisBharat (NVB)  |  Track 1: AI for Digital Public Infrastructure & Governance  |  Code for Communities 2")

        # Slide Number Pill
        self.setFont("Helvetica-Bold", 8.5)
        self.setFillColor(colors.HexColor("#1a73e8"))
        page_str = f"Slide {self._pageNumber} of {page_count}"
        self.drawRightString(756, 24, page_str)

        self.restoreState()


def build_pdf(output_path: Path):
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(letter),
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=48,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'DeckTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=21,
        leading=25,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=4,
    )

    badge_style = ParagraphStyle(
        'DeckBadge',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#1d4ed8"),
        spaceAfter=3,
    )

    lead_style = ParagraphStyle(
        'DeckLead',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10.5,
        leading=14.5,
        textColor=colors.HexColor("#475569"),
        spaceAfter=12,
    )

    card_title_style = ParagraphStyle(
        'CardTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10.5,
        leading=13.5,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=3,
    )

    card_body_style = ParagraphStyle(
        'CardBody',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#334155"),
    )

    metric_val_style = ParagraphStyle(
        'MetricVal',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=24,
        leading=28,
        textColor=colors.HexColor("#1a73e8"),
        alignment=1,
    )

    metric_val_green = ParagraphStyle(
        'MetricValGreen',
        parent=metric_val_style,
        textColor=colors.HexColor("#15803d"),
    )

    metric_val_purple = ParagraphStyle(
        'MetricValPurple',
        parent=metric_val_style,
        textColor=colors.HexColor("#7e22ce"),
    )

    metric_lbl_style = ParagraphStyle(
        'MetricLbl',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#475569"),
        alignment=1,
    )

    story = []

    def make_card(title, body, width=350, bg="#f8fafc", border="#cbd5e1"):
        content = [
            Paragraph(title, card_title_style),
            Spacer(1, 2),
            Paragraph(body, card_body_style)
        ]
        t = Table([[content]], colWidths=[width])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor(bg)),
            ('BOX', (0,0), (-1,-1), 1, colors.HexColor(border)),
            ('TOPPADDING', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('LEFTPADDING', (0,0), (-1,-1), 10),
            ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ]))
        return t

    def make_metric(val, lbl, style=metric_val_style, bg="#eff6ff", border="#bfdbfe", width=233):
        content = [
            Paragraph(val, style),
            Spacer(1, 3),
            Paragraph(lbl, metric_lbl_style)
        ]
        t = Table([[content]], colWidths=[width])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor(bg)),
            ('BOX', (0,0), (-1,-1), 1, colors.HexColor(border)),
            ('TOPPADDING', (0,0), (-1,-1), 10),
            ('BOTTOMPADDING', (0,0), (-1,-1), 10),
            ('LEFTPADDING', (0,0), (-1,-1), 6),
            ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ]))
        return t

    # ====================================================
    # SLIDE 1: Title & Executive Overview
    # ====================================================
    story.append(Paragraph("SLIDE 01 · VISION & EXECUTIVE OVERVIEW", badge_style))
    story.append(Paragraph("Nexus VisBharat: Sovereign Multilingual Civic AI", title_style))
    story.append(Paragraph("Connecting marginalized vernacular citizen voices directly with transparent municipal capital budget (Capex) allocation across 97 southern Indian districts.", lead_style))

    metrics1 = [[
        make_metric("100%", "Emergency Recall (33/33 Cases across Tamil, Telugu, and English)", metric_val_style, "#eff6ff", "#bfdbfe"),
        make_metric("0.9746", "Category Macro-F1 across 10 Departments (Gemini 3.6 Flash)", metric_val_green, "#f0fdf4", "#bbf7d0"),
        make_metric("<800ms", "Deterministic FastPath Emergency Triage Latency", metric_val_purple, "#faf5ff", "#e9d5ff"),
    ]]
    t_m1 = Table(metrics1, colWidths=[237, 237, 237])
    story.append(t_m1)
    story.append(Spacer(1, 10))

    grid1 = [[
        make_card("Track 1: AI for Digital Public Infrastructure & Governance", "Converts unstructured, regional-language citizen voice notes into explainable, objective municipal capital expenditure (Capex) recommendations.", 354),
        make_card("Statutory DPDP Act 2023 Compliance & Zero-Trust", "Automated ingress PII scrubbing (UIDAI Aadhaar, phone, PAN), Laplace Differential Privacy (ε=1.0), and tamper-evident SHA-256 audit chaining.", 354),
    ]]
    story.append(Table(grid1, colWidths=[355, 355]))
    story.append(PageBreak())

    # ====================================================
    # SLIDE 2: The Municipal Infrastructure Black Hole
    # ====================================================
    story.append(Paragraph("SLIDE 02 · THE GOVERNANCE CHALLENGE", badge_style))
    story.append(Paragraph("The Municipal Infrastructure Black Hole", title_style))
    story.append(Paragraph("Why 65% of citizen grievances in India's 4,000+ Urban Local Bodies (ULBs) fail to reach capital expenditure (Capex) allocations.", lead_style))

    grid2 = [
        [
            make_card("1. Multilingual Ingress Friction", "Citizens voice grievances in colloquial Tamil, Telugu, Tanglish, and local dialects. Existing portals lack automated multimodal Indic intake, trapping vernacular complaints in manual queues.", 354),
            make_card("2. Siloed Isolated Demands", "1,000 individual reports of contaminated drinking water or road cave-ins are treated as 1,000 isolated tickets rather than a unified capital infrastructure requirement.", 354),
        ],
        [
            make_card("3. Subjective Capex Sanctions", "Municipal budget allocations often rely on political lobbying rather than empirical citizen stress, PM Gati Shakti infrastructure corridors, or NITI Aayog deprivation data.", 354),
            make_card("4. Audit & Accountability Opacity", "No verifiable cryptographic audit trail connects an original citizen complaint to the eventual Detailed Project Report (DPR) and municipal sanction order.", 354),
        ],
    ]
    t_grid2 = Table(grid2, colWidths=[355, 355])
    t_grid2.setStyle(TableStyle([('BOTTOMPADDING', (0,0), (-1,0), 8)]))
    story.append(t_grid2)
    story.append(PageBreak())

    # ====================================================
    # SLIDE 3: 5-Layer Solution Architecture
    # ====================================================
    story.append(Paragraph("SLIDE 03 · SYSTEM ARCHITECTURE", badge_style))
    story.append(Paragraph("Five-Layer Sovereign Intelligence Architecture", title_style))
    story.append(Paragraph("An end-to-end Digital Public Infrastructure transforming vernacular signals into verified municipal works.", lead_style))

    grid3 = [
        [
            make_card("Layer 1: Omnichannel Vernacular Ingress", "Browser voice recording, Toll-free IVR, WhatsApp/Telegram bot, and keyword SMS across Tamil, Telugu, and English. In-process PII scrubbing before storage.", 354),
            make_card("Layer 2: Gemini 3.6 Flash Indic Triage", "Indic semantic parsing, 10-category tagging, 3-tier urgency classification, Voice Access Index (VAI) bias correction, and sub-second FastPath.", 354),
        ],
        [
            make_card("Layer 3: Spatial Demand Clustering", "Real-time DBSCAN geospatial clustering (500m radius) aggregates isolated grievances into unified, cost-efficient municipal works packages.", 354),
            make_card("Layer 4: National Open Data Fusion", "Fuses citizen demand with Census 2011 demographics, NITI Aayog MPI (Multidimensional Poverty), and PM Gati Shakti infrastructure nodes.", 354),
        ],
    ]
    t_grid3 = Table(grid3, colWidths=[355, 355])
    t_grid3.setStyle(TableStyle([('BOTTOMPADDING', (0,0), (-1,0), 8)]))
    story.append(t_grid3)
    story.append(Spacer(1, 6))
    card_l5 = make_card("Layer 5: Verifiable Governance & Cryptographic Ledger", "Role-Based Access Control (@require_roles) separating Citizens, Analysts, and Auditors with an append-only SHA-256 tamper-evident ledger and external timestamping (RFC 3161 / Rekor).", 718, "#eff6ff", "#bfdbfe")
    story.append(Table([[card_l5]], colWidths=[718]))
    story.append(PageBreak())

    # ====================================================
    # SLIDE 4: Omnichannel Vernacular Ingress
    # ====================================================
    story.append(Paragraph("SLIDE 04 · CITIZEN INGRESS", badge_style))
    story.append(Paragraph("Breaking Digital Literacy Barriers", title_style))
    story.append(Paragraph("Empowering every citizen to participate in democratic governance in their native tongue.", lead_style))

    grid4 = [
        [
            make_card("Web Voice & Text Portal", "In-browser Web Audio API recording, client-side waveform visualization, live transcript review, and one-click Tamil/Telugu/English language switching.", 354),
            make_card("Toll-Free IVR & Missed-Call Gateway", "Telephony integration with automated outbound callbacks for basic feature-phone users, transcribing speech via Cloud Speech-to-Text v2.", 354),
        ],
        [
            make_card("WhatsApp & Telegram Bots", "Citizens send voice notes, photos, and location pins directly to verified bots; webhook pipelines route incidents into automated triage in real time.", 354),
            make_card("Low-Bandwidth Keyword SMS", "Parses structured SMS inputs ('NVB <District> <Issue>') for rural areas with limited or no mobile broadband connectivity.", 354),
        ],
    ]
    t_grid4 = Table(grid4, colWidths=[355, 355])
    t_grid4.setStyle(TableStyle([('BOTTOMPADDING', (0,0), (-1,0), 8)]))
    story.append(t_grid4)
    story.append(PageBreak())

    # ====================================================
    # SLIDE 5: Multilingual AI Triage Benchmark
    # ====================================================
    story.append(Paragraph("SLIDE 05 · AI EVALUATION & EVIDENCE", badge_style))
    story.append(Paragraph("108-Case Held-Out Multilingual Benchmark", title_style))
    story.append(Paragraph("Empirical evaluation of Gemini 3.6 Flash across 3 languages with strict 1:1:1 emergency parity.", lead_style))

    grid5_metrics = [[
        make_metric("0.9746", "Category Macro-F1 across 10 Departments (Gemini 3.6 Flash)", metric_val_style, "#eff6ff", "#bfdbfe"),
        make_metric("86.11%", "Urgency Accuracy (Routine, Urgent, Emergency)", metric_val_green, "#f0fdf4", "#bbf7d0"),
        make_metric("33 / 33", "100% Emergency Recall (11 EN, 11 TA, 11 TE) — Zero False Negatives", metric_val_purple, "#faf5ff", "#e9d5ff"),
    ]]
    story.append(Table(grid5_metrics, colWidths=[237, 237, 237]))
    story.append(Spacer(1, 10))

    grid5 = [[
        make_card("1:1:1 Linguistic Emergency Parity", "Evaluated against 33 high-hazard life-safety scenarios: 11 in English, 11 in Tamil, and 11 in Telugu. Gemini Flash achieved 11/11 in EN, 11/11 in TA, and 11/11 in TE.", 354),
        make_card("Sub-Second Deterministic FastPath", "Life-safety hazards (live power line down, gas leak, bridge structural failure) trigger deterministic regex-gated routing in <800ms, bypassing cloud model latency.", 354),
    ]]
    story.append(Table(grid5, colWidths=[355, 355]))
    story.append(PageBreak())

    # ====================================================
    # SLIDE 6: DPDP Act 2023 Compliance & Privacy
    # ====================================================
    story.append(Paragraph("SLIDE 06 · REGULATORY COMPLIANCE", badge_style))
    story.append(Paragraph("India DPDP Act 2023 Compliance Architecture", title_style))
    story.append(Paragraph("Engineering controls fulfilling statutory mandates under Act No. 22 of 2023 (Republic of India).", lead_style))

    grid6 = [
        [
            make_card("Automated Ingress PII Scrubber", "Executes in-process before database storage or LLM prompts. Strips 12-digit Aadhaar numbers (Verhoeff verified), 10-digit mobile numbers, PAN, and voter IDs into safe tokens.", 354),
            make_card("Differential Privacy in Spatial Maps (ε=1.0)", "Laplace mechanism adds calibrated noise to ward-level complaint counts, mathematically guaranteeing protection against membership inference attacks.", 354),
        ],
        [
            make_card("Section 5 & 6 Consent Gateway", "Granular multilingual notices in Tamil, Telugu, and English. Opt-in controls for triage routing vs. analytical aggregation with self-service revocation.", 354),
            make_card("Section 12 Right to Erasure & Correction", "Verified OTP endpoints allow citizens to correct errors or trigger cryptographic zeroization/tombstoning of personal records post-resolution.", 354),
        ],
    ]
    t_grid6 = Table(grid6, colWidths=[355, 355])
    t_grid6.setStyle(TableStyle([('BOTTOMPADDING', (0,0), (-1,0), 8)]))
    story.append(t_grid6)
    story.append(PageBreak())

    # ====================================================
    # SLIDE 7: STRIDE Security Threat Model
    # ====================================================
    story.append(Paragraph("SLIDE 07 · CYBERSECURITY ARCHITECTURE", badge_style))
    story.append(Paragraph("STRIDE Security Threat Model & Defense Matrix", title_style))
    story.append(Paragraph("Defense-in-depth architecture protecting citizen data across 5 distinct trust boundaries.", lead_style))

    grid7 = [
        [
            make_card("Spoofing & Elevation of Privilege", "Mitigation: PBKDF2/SHA-256 token hashing in visbharat/security.py, signed session tokens, and strict server-side role validation (@require_roles).", 354),
            make_card("Tampering & Repudiation", "Mitigation: Append-only SHA-256 linear hash chain links every record: modifying any historical event invalidates the ledger. Audit exports support third-party verification.", 354),
        ],
        [
            make_card("Information Disclosure", "Mitigation: In-process PII scrubbing before database persistence; public APIs only expose ward-level differentially private metrics.", 354),
            make_card("Denial of Service (DoS)", "Mitigation: Cloud Armor WAF, Flask-Limiter rate throttling per IP/phone, deterministic fastpath caching, and strict 32KB request payload limits.", 354),
        ],
    ]
    t_grid7 = Table(grid7, colWidths=[355, 355])
    t_grid7.setStyle(TableStyle([('BOTTOMPADDING', (0,0), (-1,0), 8)]))
    story.append(t_grid7)
    story.append(PageBreak())

    # ====================================================
    # SLIDE 8: Spatial Demand Clustering & FastPath
    # ====================================================
    story.append(Paragraph("SLIDE 08 · SPATIAL INTELLIGENCE", badge_style))
    story.append(Paragraph("DBSCAN Geospatial Clustering & Velocity", title_style))
    story.append(Paragraph("Aggregating thousands of noisy reports into prioritized capital project candidates.", lead_style))

    grid8 = [
        [
            make_card("500m DBSCAN Incident Clustering", "Groups co-located grievances within a 500m radius into unified capital projects (e.g. replacing an entire corroded water main instead of patching 50 individual pipe leaks).", 354),
            make_card("7-Day Demand Velocity Tracking", "Monitors rates of change across wards to detect emerging infrastructure crises (e.g. post-monsoon sewage contamination spikes) before catastrophic failure.", 354),
        ],
        [
            make_card("Voice Access Index (VAI) Weighting", "Applies inverse-density weights to compensate for lower digital smartphone penetration in rural and marginalized communities, preventing urban digital bias.", 354),
            make_card("Silence Map Anomaly Detection", "Identifies high-poverty wards with abnormally low complaint counts, proactively triggering field worker outreach to bridge reporting deficits.", 354),
        ],
    ]
    t_grid8 = Table(grid8, colWidths=[355, 355])
    t_grid8.setStyle(TableStyle([('BOTTOMPADDING', (0,0), (-1,0), 8)]))
    story.append(t_grid8)
    story.append(PageBreak())

    # ====================================================
    # SLIDE 9: Open Data DPI Fusion
    # ====================================================
    story.append(Paragraph("SLIDE 09 · DATA FUSION", badge_style))
    story.append(Paragraph("National Digital Public Infrastructure (DPI) Fusion", title_style))
    story.append(Paragraph("Grounding citizen voice in authoritative national datasets across 97 southern Indian districts.", lead_style))

    grid9 = [
        [
            make_card("Census 2011 & SECC Deprivation", "Integrates ward population density, rural/urban ratios, and Socio-Economic and Caste Census deprivation indices.", 354),
            make_card("NITI Aayog MPI & SDG Index", "Fuses Multidimensional Poverty Index (MPI 2023) and SDG India Index indicators (SDG 6 Clean Water, SDG 3 Good Health).", 354),
        ],
        [
            make_card("PM Gati Shakti Infrastructure Nodes", "Correlates citizen demands with road networks, logistics parks, power grid coverage, and State Budget Outlays (2025-26).", 354),
            make_card("Reproducible ML Training Pipeline", "Layer 4 XGBoost booster (model.bst) trained on fused indicators via scripts/train_stress_model.py, verified with SHA-256 checks.", 354),
        ],
    ]
    t_grid9 = Table(grid9, colWidths=[355, 355])
    t_grid9.setStyle(TableStyle([('BOTTOMPADDING', (0,0), (-1,0), 8)]))
    story.append(t_grid9)
    story.append(PageBreak())

    # ====================================================
    # SLIDE 10: Policy Analyst Workbench
    # ====================================================
    story.append(Paragraph("SLIDE 10 · DECISION SUPPORT", badge_style))
    story.append(Paragraph("Policy Analyst Multi-Criteria Optimization", title_style))
    story.append(Paragraph("Explainable decision support balancing Citizen Demand, Social Equity, and Infrastructure Gaps.", lead_style))

    grid10 = [
        [
            make_card("Multi-Criteria Algorithmic Ranking", "Transparent scoring formula: Priority = w1(Demand) + w2(Equity/MPI) + w3(Coverage Gap). Officers adjust weights dynamically with real-time sensitivity analysis.", 354),
            make_card("Dynamic Budget Allocation Simulation", "Knapsack optimization simulates project selection under arbitrary municipal budget caps (e.g. ₹500 Lakh cap), maximizing total public utility.", 354),
        ],
        [
            make_card("Detailed Project Dossiers", "One-click deep dive for every project: review all linked citizen complaints, GIS boundaries, cost estimates, and suggested funding schemes.", 354),
            make_card("Human-in-the-Loop Governance", "AI assists prioritization; human municipal officers authorize spending. Every approval is cryptographically signed and logged.", 354),
        ],
    ]
    t_grid10 = Table(grid10, colWidths=[355, 355])
    t_grid10.setStyle(TableStyle([('BOTTOMPADDING', (0,0), (-1,0), 8)]))
    story.append(t_grid10)
    story.append(PageBreak())

    # ====================================================
    # SLIDE 11: Auditor Cryptographic Ledger & External Anchoring
    # ====================================================
    story.append(Paragraph("SLIDE 11 · VERIFIABLE ACCOUNTABILITY", badge_style))
    story.append(Paragraph("Tamper-Evident Ledger & External Anchoring", title_style))
    story.append(Paragraph("Cryptographic accountability satisfying Indian evidentiary standards (IT Act 2000).", lead_style))

    grid11 = [
        [
            make_card("Internal SHA-256 Hash Chaining", "Every intake, status transition, and sanction order computes H_i = SHA256(H_{i-1} || t || actor || action || payload). Recomputed and verified via /api/v2/auditor/integrity.", 354),
            make_card("Epoch Merkle Tree Batching", "Ledger entries batched hourly into a binary Merkle tree, producing a single root hash (M_epoch) for cost-efficient external verification.", 354),
        ],
        [
            make_card("RFC 3161 Trusted Timestamp Authority", "Submits Merkle roots to accredited Indian CAs (e-Mudhra/CDAC) for legally valid X.509 cryptographic timestamp tokens (TST).", 354),
            make_card("Sigstore Rekor Public Transparency Log", "Commits root hashes to an open, append-only transparency log (rekor.sigstore.dev), enabling public civil-society verification.", 354),
        ],
    ]
    t_grid11 = Table(grid11, colWidths=[355, 355])
    t_grid11.setStyle(TableStyle([('BOTTOMPADDING', (0,0), (-1,0), 8)]))
    story.append(t_grid11)
    story.append(PageBreak())

    # ====================================================
    # SLIDE 12: Production Cloud Architecture & Pilot Roadmap
    # ====================================================
    story.append(Paragraph("SLIDE 12 · PRODUCTION CLOUD ARCHITECTURE", badge_style))
    story.append(Paragraph("Google Cloud Production & Pilot Deployment", title_style))
    story.append(Paragraph("Production-ready sovereign infrastructure deployed in asia-south1 (Mumbai) serving 100% of live traffic.", lead_style))

    metrics12 = [[
        make_metric("341 / 341", "Passing Automated Unit & Integration Tests (100%)", metric_val_style, "#eff6ff", "#bfdbfe"),
        make_metric("13 / 13", "Passing Live Cloud Run Authenticated Acceptance Checks", metric_val_green, "#f0fdf4", "#bbf7d0"),
        make_metric("97", "Southern Grid Districts (TN: 38, AP: 26, TS: 33)", metric_val_purple, "#faf5ff", "#e9d5ff"),
    ]]
    story.append(Table(metrics12, colWidths=[237, 237, 237]))
    story.append(Spacer(1, 10))

    grid12 = [[
        make_card("Live Google Cloud Stack", "Google Cloud Run (serverless container) + Cloud SQL PostgreSQL (nvb-postgres) + Google Secret Manager + BigQuery + Gemini 3.6 Flash.", 354),
        make_card("Vellore–Tirupati Pilot Corridor & Human Capital ROI", "Tested cross-state water corridor. Empirical WASH elasticity models project a 14.2% reduction in waterborne morbidity and significant lifetime earnings uplift.", 354),
    ]]
    story.append(Table(grid12, colWidths=[355, 355]))

    # Build PDF with PresentationCanvas
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.build(story, canvasmaker=PresentationCanvas)
    print(f"Successfully generated presentation PDF: {output_path} ({output_path.stat().st_size} bytes)")


if __name__ == '__main__':
    import shutil
    repo_root = Path(__file__).resolve().parent.parent
    out_file = repo_root / "docs" / "NVB-pitch-deck.pdf"
    build_pdf(out_file)
    root_file = repo_root / "NVB-pitch-deck.pdf"
    shutil.copyfile(out_file, root_file)
    print(f"Copied to root: {root_file} ({root_file.stat().st_size} bytes)")
