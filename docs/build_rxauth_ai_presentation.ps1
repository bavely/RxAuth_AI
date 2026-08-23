param(
    [string]$OutputDirectory = $PSScriptRoot
)

$ErrorActionPreference = 'Stop'

function Color([string]$hex) {
    $value = $hex.TrimStart('#')
    $r = [Convert]::ToInt32($value.Substring(0, 2), 16)
    $g = [Convert]::ToInt32($value.Substring(2, 2), 16)
    $b = [Convert]::ToInt32($value.Substring(4, 2), 16)
    return $r + ($g * 256) + ($b * 65536)
}

$C = @{
    Navy = Color '#071B33'
    Ink = Color '#172B4D'
    Blue = Color '#0F62FE'
    Blue2 = Color '#4589FF'
    Cyan = Color '#33B1FF'
    Teal = Color '#009D9A'
    Green = Color '#24A148'
    Yellow = Color '#F1C21B'
    Orange = Color '#FF832B'
    Red = Color '#DA1E28'
    Purple = Color '#8A3FFC'
    White = Color '#FFFFFF'
    Snow = Color '#F7F9FC'
    Cloud = Color '#E8EEF5'
    Mid = Color '#6F7E91'
    PaleBlue = Color '#EAF2FF'
    PaleGreen = Color '#E8F5EC'
    PaleYellow = Color '#FFF8D6'
    PaleRed = Color '#FDECEF'
}

$script:SlideWidth = 960
$script:SlideHeight = 540
$script:Deck = $null

function Add-Text {
    param($Slide, [string]$Text, [double]$X, [double]$Y, [double]$W, [double]$H,
        [double]$Size = 18, [int]$ColorValue = $C.Ink, [bool]$Bold = $false,
        [int]$Align = 1, [string]$Font = 'Aptos', [int]$VAlign = 1)
    $shape = $Slide.Shapes.AddTextbox(1, $X, $Y, $W, $H)
    $shape.TextFrame.MarginLeft = 0
    $shape.TextFrame.MarginRight = 0
    $shape.TextFrame.MarginTop = 0
    $shape.TextFrame.MarginBottom = 0
    $shape.TextFrame.WordWrap = -1
    $shape.TextFrame.VerticalAnchor = $VAlign
    $range = $shape.TextFrame.TextRange
    $range.Text = $Text
    $range.Font.Name = $Font
    $range.Font.Size = $Size
    $range.Font.Color.RGB = $ColorValue
    $range.Font.Bold = $(if ($Bold) { -1 } else { 0 })
    $range.ParagraphFormat.Alignment = $Align
    return $shape
}

function Add-Rect {
    param($Slide, [double]$X, [double]$Y, [double]$W, [double]$H,
        [int]$Fill, [double]$Radius = 0, [int]$Line = -1, [double]$Transparency = 0)
    $shapeType = $(if ($Radius -gt 0) { 5 } else { 1 })
    $shape = $Slide.Shapes.AddShape($shapeType, $X, $Y, $W, $H)
    $shape.Fill.Solid()
    $shape.Fill.ForeColor.RGB = $Fill
    $shape.Fill.Transparency = $(if ($Transparency -gt 1) { $Transparency / 100 } else { $Transparency })
    if ($Line -lt 0) {
        $shape.Line.Visible = 0
    } else {
        $shape.Line.Visible = -1
        $shape.Line.ForeColor.RGB = $Line
        $shape.Line.Weight = 1
    }
    return $shape
}

function Add-Line {
    param($Slide, [double]$X1, [double]$Y1, [double]$X2, [double]$Y2,
        [int]$ColorValue = $C.Cloud, [double]$Weight = 1.5, [bool]$Arrow = $false)
    $shape = $Slide.Shapes.AddLine($X1, $Y1, $X2, $Y2)
    $shape.Line.ForeColor.RGB = $ColorValue
    $shape.Line.Weight = $Weight
    if ($Arrow) { $shape.Line.EndArrowheadStyle = 3 }
    return $shape
}

function Add-Pill {
    param($Slide, [string]$Text, [double]$X, [double]$Y, [double]$W,
        [int]$Fill = $C.PaleBlue, [int]$TextColor = $C.Blue)
    Add-Rect $Slide $X $Y $W 24 $Fill 8 | Out-Null
    Add-Text $Slide $Text ($X + 8) ($Y + 5) ($W - 16) 15 10 $TextColor $true 2 | Out-Null
}

function Add-BulletList {
    param($Slide, [string[]]$Items, [double]$X, [double]$Y, [double]$W,
        [double]$FontSize = 17, [double]$Gap = 42, [int]$Accent = $C.Blue)
    for ($i = 0; $i -lt $Items.Count; $i++) {
        $yy = $Y + ($i * $Gap)
        $dot = $Slide.Shapes.AddShape(9, $X, $yy + 6, 9, 9)
        $dot.Fill.Solid(); $dot.Fill.ForeColor.RGB = $Accent; $dot.Line.Visible = 0
        Add-Text $Slide $Items[$i] ($X + 20) $yy ($W - 20) ($Gap - 4) $FontSize $C.Ink $false | Out-Null
    }
}

function Add-Card {
    param($Slide, [string]$Title, [string]$Body, [double]$X, [double]$Y,
        [double]$W, [double]$H, [int]$Accent = $C.Blue, [string]$Metric = '')
    Add-Rect $Slide $X $Y $W $H $C.White 8 $C.Cloud | Out-Null
    Add-Rect $Slide $X $Y 6 $H $Accent 4 | Out-Null
    if ($Metric) {
        Add-Text $Slide $Metric ($X + 20) ($Y + 16) ($W - 35) 40 26 $Accent $true | Out-Null
        Add-Text $Slide $Title ($X + 20) ($Y + 58) ($W - 35) 25 13 $C.Ink $true | Out-Null
        Add-Text $Slide $Body ($X + 20) ($Y + 88) ($W - 35) ($H - 98) 12 $C.Mid $false | Out-Null
    } else {
        Add-Text $Slide $Title ($X + 20) ($Y + 18) ($W - 35) 28 15 $C.Ink $true | Out-Null
        Add-Text $Slide $Body ($X + 20) ($Y + 55) ($W - 35) ($H - 65) 12.5 $C.Mid $false | Out-Null
    }
}

function New-Slide {
    param([string]$Section, [string]$Title, [string]$Subtitle = '')
    $slide = $script:Deck.Slides.Add($script:Deck.Slides.Count + 1, 12)
    $slide.FollowMasterBackground = 0
    $slide.Background.Fill.Solid()
    $slide.Background.Fill.ForeColor.RGB = $C.Snow
    Add-Rect $slide 0 0 12 $script:SlideHeight $C.Blue | Out-Null
    Add-Text $slide $Section 42 22 330 20 10 $C.Blue $true | Out-Null
    Add-Text $slide $Title 42 48 850 46 27 $C.Navy $true | Out-Null
    if ($Subtitle) { Add-Text $slide $Subtitle 42 96 850 28 12.5 $C.Mid $false | Out-Null }
    Add-Line $slide 42 510 918 510 $C.Cloud 1 | Out-Null
    Add-Text $slide 'RxAuth AI  |  IBM AI Engineering course project' 42 517 500 14 8.5 $C.Mid | Out-Null
    Add-Text $slide ([string]$script:Deck.Slides.Count) 880 517 38 14 8.5 $C.Mid $true 3 | Out-Null
    return $slide
}

function Set-Notes {
    param($Slide, [string]$Text)
    try {
        foreach ($shape in $Slide.NotesPage.Shapes) {
            if ($shape.Type -eq 14 -and $shape.PlaceholderFormat.Type -eq 2) {
                $shape.TextFrame.TextRange.Text = $Text
                break
            }
        }
    } catch {
        Write-Warning "Could not add notes to slide $($Slide.SlideIndex): $($_.Exception.Message)"
    }
}

function Add-FlowBox {
    param($Slide, [string]$Label, [double]$X, [double]$Y, [double]$W, [double]$H,
        [int]$Fill = $C.White, [int]$TextColor = $C.Ink, [int]$Border = $C.Cloud)
    Add-Rect $Slide $X $Y $W $H $Fill 8 $Border | Out-Null
    Add-Text $Slide $Label ($X + 10) ($Y + 8) ($W - 20) ($H - 16) 12.5 $TextColor $true 2 'Aptos' 3 | Out-Null
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$pptPath = Join-Path $OutputDirectory 'RxAuth_AI_IBM_AI_Engineering_Course_Project.pptx'
$pdfPath = Join-Path $OutputDirectory 'RxAuth_AI_IBM_AI_Engineering_Course_Project.pdf'

# PowerPoint's COM SaveAs does not reliably prompt-free overwrite an existing file.
# These are the two exact generated outputs of this script, so replace them explicitly.
if (Test-Path -LiteralPath $pptPath) { Remove-Item -LiteralPath $pptPath -Force }
if (Test-Path -LiteralPath $pdfPath) { Remove-Item -LiteralPath $pdfPath -Force }

$powerPoint = New-Object -ComObject PowerPoint.Application
$powerPoint.Visible = -1
$script:Deck = $powerPoint.Presentations.Add()
$script:Deck.PageSetup.SlideWidth = $script:SlideWidth
$script:Deck.PageSetup.SlideHeight = $script:SlideHeight

try {
    # 1 — Cover
    $s = $script:Deck.Slides.Add(1, 12)
    $s.FollowMasterBackground = 0
    $s.Background.Fill.Solid(); $s.Background.Fill.ForeColor.RGB = $C.Navy
    Add-Rect $s 0 0 16 540 $C.Blue | Out-Null
    Add-Rect $s 620 -80 410 410 $C.Blue 0 -1 15 | Out-Null
    Add-Rect $s 690 240 330 330 $C.Purple 0 -1 25 | Out-Null
    for ($i = 0; $i -lt 6; $i++) {
        $x = 650 + (($i % 3) * 95); $y = 72 + ([math]::Floor($i / 3) * 120)
        $node = $s.Shapes.AddShape(9, $x, $y, 54, 54)
        $node.Fill.Solid(); $node.Fill.ForeColor.RGB = $(if ($i -eq 3) { $C.Cyan } else { $C.White })
        $node.Fill.Transparency = [single]$(if ($i -eq 3) { 0.0 } else { 0.08 }); $node.Line.Visible = 0
        if ($i -lt 5) { Add-Line $s ($x + 54) ($y + 27) ($x + 94) ($y + 64) $C.Cyan 2 | Out-Null }
    }
    Add-Pill $s 'IBM AI ENGINEERING COURSE PROJECT' 54 54 270 $C.Blue $C.White
    Add-Text $s 'RxAuth AI' 54 124 540 62 38 $C.White $true | Out-Null
    Add-Text $s 'Evidence-grounded prior authorization intelligence' 54 190 545 86 25 $C.Cyan $true | Out-Null
    Add-Text $s 'A human-in-the-loop AI engineering prototype for specialty pharmacy workflows' 54 292 520 56 16 $C.White | Out-Null
    Add-Line $s 54 382 480 382 $C.Blue2 2 | Out-Null
    Add-Text $s 'Bavely S. Tawfik' 54 403 300 24 15 $C.White $true | Out-Null
    Add-Text $s 'Python • scikit-learn • OpenCV • Pydantic • reproducible evaluation' 54 435 540 22 11.5 $C.Cloud | Out-Null
    Add-Text $s 'August 2026' 54 490 200 18 10 $C.Cloud | Out-Null
    Set-Notes $s 'Opening: RxAuth AI is an evidence-grounded prior-authorization intelligence prototype. It is designed as an IBM AI Engineering course project and as a portfolio system that can grow from classical machine learning into retrieval-augmented generation and controlled agentic workflows. The central idea is simple: every important answer must be traceable, uncertainty must be explicit, and a human remains responsible for the final action.'

    # 2 — Executive summary
    $s = New-Slide '01  /  PROJECT OVERVIEW' 'Executive summary' 'One project demonstrating the complete AI engineering lifecycle'
    Add-Card $s 'The problem' 'Prior-authorization packets are document-heavy, policy-dependent, and vulnerable to missing or ambiguous evidence.' 42 146 270 150 $C.Orange '01'
    Add-Card $s 'The solution' 'Ingest and classify documents, match patient evidence to policy criteria, expose gaps, and enforce provenance before review.' 345 146 270 150 $C.Blue '02'
    Add-Card $s 'Current proof' 'Phase 1.5 includes typed ingestion, a leakage-resistant ML baseline, an end-to-end case spine, tests, and reports.' 648 146 270 150 $C.Green '03'
    Add-Rect $s 42 325 876 130 $C.Navy 8 | Out-Null
    Add-Text $s 'PROJECT THESIS' 66 346 150 20 10 $C.Cyan $true | Out-Null
    Add-Text $s 'Use probabilistic AI for interpretation; use deterministic code for explicit rules; route uncertainty to a human.' 66 378 810 55 22 $C.White $true | Out-Null
    Set-Notes $s 'The project addresses administrative preparation, not clinical decision-making. The implemented Phase 1.5 foundation spans data generation, PDF and image ingestion, a classical text classifier, confidence-based review routing, deterministic criteria evaluation, provenance, groundedness checks, and automated testing. The roadmap adds deep learning, extraction, policy retrieval, and constrained generation without changing the safety contract.'

    # 3 — Problem
    $s = New-Slide '02  /  PROBLEM' 'Why prior authorization needs intelligence, not just automation' 'The bottleneck is evidence completeness and traceability before submission'
    Add-FlowBox $s 'Patient packet' 55 158 140 58 $C.PaleBlue $C.Blue $C.Blue2
    Add-Line $s 195 187 250 187 $C.Blue2 2 $true | Out-Null
    Add-FlowBox $s 'Multiple document types' 250 158 160 58
    Add-Line $s 410 187 465 187 $C.Blue2 2 $true | Out-Null
    Add-FlowBox $s 'Payer policy criteria' 465 158 160 58
    Add-Line $s 625 187 680 187 $C.Blue2 2 $true | Out-Null
    Add-FlowBox $s 'Human reviewer' 680 158 160 58 $C.PaleGreen $C.Green $C.Green
    Add-Card $s 'Fragmented inputs' 'Clinical notes, labs, prescriptions, referrals, insurance cards, histories, forms, and miscellaneous correspondence.' 55 270 260 158 $C.Orange
    Add-Card $s 'Policy translation' 'Natural-language requirements must become explicit checks while preserving payer, version, page, and source text.' 350 270 260 158 $C.Purple
    Add-Card $s 'Trust gap' 'A confident-sounding answer is not enough; reviewers need evidence, citations, missing-data flags, and calibrated uncertainty.' 645 270 260 158 $C.Red
    Set-Notes $s 'The work happens across heterogeneous documents and a separate policy source. A useful system cannot merely summarize. It must identify which requirement is being evaluated, select the right patient evidence, normalize values, determine when a rule can be computed, and show the reviewer where each result came from. This is why RxAuth AI treats provenance and uncertainty as product features, not afterthoughts.'

    # 4 — Scope and safety
    $s = New-Slide '03  /  RESPONSIBLE AI' 'Scope and safety guardrails' 'The portfolio version is administrative decision-support using synthetic data only'
    Add-Pill $s 'IN SCOPE' 50 145 120 $C.PaleGreen $C.Green
    Add-BulletList $s @('Prepare a case for human review','Classify and organize synthetic documents','Match evidence to explicit policy criteria','Flag missing, ambiguous, or low-confidence evidence','Preserve source provenance end to end') 52 185 390 15 48 $C.Green
    Add-Pill $s 'OUT OF SCOPE' 515 145 140 $C.PaleRed $C.Red
    Add-BulletList $s @('Diagnose, prescribe, or recommend treatment','Approve or deny medical necessity','Use real PHI in the public portfolio','Infer live member benefit status from policy text','Submit autonomously to a payer') 517 185 390 15 48 $C.Red
    Add-Rect $s 50 445 855 38 $C.PaleYellow 6 | Out-Null
    Add-Text $s 'Human review is mandatory: model output never becomes submission-ready by itself.' 68 455 820 20 13 $C.Ink $true | Out-Null
    Set-Notes $s 'These boundaries are important for both responsible AI and honest course evaluation. The public project uses fully synthetic identities and documents. It does not make a clinical decision or claim production validity. A future commercial deployment would require a separate HIPAA-ready architecture, access controls, encryption, auditing, retention policy, and governance.'

    # 5 — Workflow
    $s = New-Slide '04  /  SYSTEM DESIGN' 'End-to-end AI workflow' 'A linear reviewer-facing flow with controlled intelligence inside each stage'
    $labels = @('1  Ingest','2  Classify','3  Extract','4  Retrieve policy','5  Structure criteria','6  Match evidence','7  Groundness gate','8  Human review')
    $fills = @($C.PaleBlue,$C.PaleBlue,$C.PaleBlue,$C.PaleYellow,$C.PaleYellow,$C.PaleGreen,$C.PaleRed,$C.PaleGreen)
    $tcolors = @($C.Blue,$C.Blue,$C.Blue,$C.Orange,$C.Orange,$C.Green,$C.Red,$C.Green)
    for ($i = 0; $i -lt 8; $i++) {
        $row = [math]::Floor($i / 4); $col = $i % 4
        $x = 52 + ($col * 220); $y = 160 + ($row * 120)
        Add-FlowBox $s $labels[$i] $x $y 170 58 $fills[$i] $tcolors[$i] $tcolors[$i]
        if ($col -lt 3) { Add-Line $s ($x + 170) ($y + 29) ($x + 210) ($y + 29) $C.Mid 1.5 $true | Out-Null }
        elseif ($row -eq 0) { Add-Line $s ($x + 85) ($y + 58) ($x + 85) ($y + 106) $C.Mid 1.5 $true | Out-Null }
    }
    Add-Rect $s 52 410 830 56 $C.Navy 8 | Out-Null
    Add-Text $s 'IMPLEMENTED NOW' 72 427 120 16 9 $C.Cyan $true | Out-Null
    Add-Text $s 'Typed ingestion + classification + deterministic matching + provenance gate' 205 421 650 28 14 $C.White $true | Out-Null
    Set-Notes $s 'The top-level workflow stays intentionally linear. In Phase 1.5, the implemented path covers typed ingestion, classification, deterministic matching on a synthetic case fixture, and a structural groundedness gate. Extraction, public payer-policy RAG, criteria extraction, and generated drafting are roadmap stages. This separation prevents the presentation from overstating what the current code does.'

    # 6 — Data model
    $s = New-Slide '05  /  DATA ENGINEERING' 'Typed entities make provenance enforceable' 'Important state lives in validated structures, not only inside prompts'
    $entities = @(
        @('Case','payer • medication • indication • documents'),
        @('Document','type • confidence • page count'),
        @('Evidence','normalized value • confidence • source span'),
        @('Policy','version • effective date • criteria'),
        @('Criterion','operator • expected value • policy source'),
        @('Evaluation','five-state result • evidence IDs • explanation')
    )
    for ($i = 0; $i -lt $entities.Count; $i++) {
        $row = [math]::Floor($i / 3); $col = $i % 3
        Add-Card $s $entities[$i][0] $entities[$i][1] (48 + $col * 300) (148 + $row * 145) 270 112 $(if ($i -eq 5) { $C.Green } else { $C.Blue })
    }
    Add-Text $s 'Provenance contract: document + page + source text + confidence + method' 65 456 820 23 14 $C.Navy $true 2 | Out-Null
    Set-Notes $s 'Pydantic models define the major entities and validate their contracts. Evidence includes a normalized value plus provenance. Policy criteria preserve source text and page. Evaluations record the selected evidence IDs, result state, confidence, method, explanation, and both policy and patient sources. This design supports testability, auditability, and later model versioning.'

    # 7 — Ingestion
    $s = New-Slide '06  /  INGESTION' 'From files to page-level text' 'One typed contract for text, text-bearing PDF, and scanned-image paths'
    $steps = @('Decode / parse','Grayscale','Denoise','Deskew','Otsu threshold','OCR adapter')
    for ($i = 0; $i -lt $steps.Count; $i++) {
        $x = 42 + ($i * 147)
        Add-FlowBox $s $steps[$i] $x 150 118 46 $(if ($i -eq 5) { $C.PaleYellow } else { $C.White }) $(if ($i -eq 5) { $C.Orange } else { $C.Blue })
        if ($i -lt 5) { Add-Line $s ($x + 118) 173 ($x + 140) 173 $C.Blue2 1.25 $true | Out-Null }
    }
    $img1 = Join-Path $projectRoot 'data/rendered/clinical_note/doc_0000_clean.png'
    $img2 = Join-Path $projectRoot 'data/rendered/lab_report/doc_0001_rotated.png'
    if (Test-Path $img1) { $s.Shapes.AddPicture($img1, 0, -1, 52, 242, 175, 190) | Out-Null }
    if (Test-Path $img2) { $s.Shapes.AddPicture($img2, 0, -1, 252, 242, 175, 190) | Out-Null }
    Add-Card $s 'Rendered benchmark' '16 text-bearing PDFs + 16 scan-like images. Degradations include clean, rotated, blurred, low-contrast, and noisy variants.' 462 242 205 190 $C.Blue '32'
    Add-Card $s 'Measured honestly' 'PDF mean character error rate: 0.000. Image preprocessing: 100%. OCR character accuracy: not reported without a configured runtime.' 695 242 205 190 $C.Orange '0.000'
    Set-Notes $s 'The ingestion layer supports UTF-8 text, Markdown, text-bearing PDFs through pypdf, and image files through OpenCV preprocessing. Every page records text, extraction method, confidence, and page number. Scanned PDFs without a text layer fail explicitly instead of becoming empty evidence. The benchmark reports what is measured and explicitly withholds OCR text accuracy until an OCR runtime is configured.'

    # 8 — Dataset
    $s = New-Slide '07  /  EXPERIMENT DESIGN' 'Synthetic dataset with leakage-resistant splits' 'Cases and template families are mutually exclusive across partitions'
    $segments = @(
        @('TRAIN','336',70,$C.Blue), @('VAL','48',390,$C.Cyan), @('TEST','48',545,$C.Teal), @('CHALLENGE','48',700,$C.Orange)
    )
    foreach ($seg in $segments) {
        Add-Rect $s $seg[2] 165 $(if ($seg[0] -eq 'TRAIN') { 290 } else { 125 }) 76 $seg[3] 6 | Out-Null
        Add-Text $s $seg[1] ($seg[2] + 10) 177 $(if ($seg[0] -eq 'TRAIN') { 270 } else { 105 }) 26 20 $C.White $true 2 | Out-Null
        Add-Text $s $seg[0] ($seg[2] + 10) 210 $(if ($seg[0] -eq 'TRAIN') { 270 } else { 105 }) 16 9 $C.White $true 2 | Out-Null
    }
    Add-Text $s '480 documents  •  8 balanced classes  •  10 template families  •  deterministic seed 42' 70 265 790 24 16 $C.Navy $true 2 | Out-Null
    Add-Card $s 'Primary test' 'Held-out family 08 is the scientific comparison point. The vectorizer is fit on training data only.' 70 320 245 125 $C.Teal
    Add-Card $s 'Challenge set' 'Family 09 adds unseen framing, cross-class noise, and deterministic OCR-like corruption.' 350 320 245 125 $C.Orange
    Add-Card $s 'Leakage defense' 'The loader rejects overlapping case IDs or template-family IDs before training.' 630 320 245 125 $C.Purple
    Set-Notes $s 'The classifier corpus contains 480 fully synthetic documents across eight classes. The split is grouped by both case and template family to reduce memorization through repeated templates. Families zero through six train the model, family seven is validation, family eight is the primary test, and family nine is a harder challenge set used for robustness analysis rather than model selection.'

    # 9 — Model
    $s = New-Slide '08  /  MACHINE LEARNING' 'Classical baseline: TF-IDF + logistic regression' 'A fast, interpretable benchmark before introducing a transformer'
    Add-FlowBox $s 'Document text' 55 175 145 58 $C.White $C.Ink
    Add-Line $s 200 204 250 204 $C.Blue2 2 $true | Out-Null
    Add-FlowBox $s "TF-IDF features`nunigrams + bigrams" 250 168 175 72 $C.PaleBlue $C.Blue $C.Blue
    Add-Line $s 425 204 475 204 $C.Blue2 2 $true | Out-Null
    Add-FlowBox $s "Balanced logistic`nregression" 475 168 175 72 $C.PaleBlue $C.Blue $C.Blue
    Add-Line $s 650 204 700 204 $C.Blue2 2 $true | Out-Null
    Add-FlowBox $s "Class probability +`nreview route" 700 168 180 72 $C.PaleGreen $C.Green $C.Green
    Add-Card $s 'Feature design' 'Lowercase English text, stop-word removal, 1–2 grams, minimum document frequency 2, sublinear term frequency.' 55 305 260 135 $C.Blue
    Add-Card $s 'Training design' 'Balanced class weights, maximum 1,000 iterations, random state 42, training split only.' 350 305 260 135 $C.Purple
    Add-Card $s 'Decision policy' 'A 0.65 confidence threshold routes uncertain predictions to human review; it is not clinically validated.' 645 305 260 135 $C.Orange
    Set-Notes $s 'The baseline is deliberately classical. TF-IDF turns unstructured text into sparse unigram and bigram features. Logistic regression estimates class probabilities across eight document types. The model is easy to train, serialize, inspect, and compare against a future transformer. The review threshold is operational for this synthetic benchmark and should not be described as clinically validated.'

    # 10 — Results
    $s = New-Slide '09  /  RESULTS' 'Classifier performance: strong test, harder challenge' 'Macro F1 is the primary balanced metric; all values come from reproducible reports'
    $names = @('Validation','Test','Challenge')
    $vals = @(0.936,0.979,0.916)
    $cols = @($C.Cyan,$C.Green,$C.Orange)
    for ($i = 0; $i -lt 3; $i++) {
        $x = 95 + ($i * 260); $barH = 250 * $vals[$i]
        Add-Rect $s $x (440 - $barH) 130 $barH $cols[$i] 5 | Out-Null
        Add-Text $s ('{0:P1}' -f $vals[$i]) ($x - 5) (410 - $barH) 140 30 21 $cols[$i] $true 2 | Out-Null
        Add-Text $s $names[$i] ($x - 5) 448 140 20 12 $C.Ink $true 2 | Out-Null
    }
    Add-Line $s 60 440 850 440 $C.Mid 1.5 | Out-Null
    Add-Rect $s 780 165 140 170 $C.Navy 8 | Out-Null
    Add-Text $s '0.004' 800 188 100 35 24 $C.Cyan $true 2 | Out-Null
    Add-Text $s 'ms / document' 800 228 100 24 11 $C.White $true 2 | Out-Null
    Add-Text $s 'CPU batch inference' 798 270 104 35 10 $C.Cloud $false 2 | Out-Null
    Add-Pill $s 'TEST CONFUSION: 1 / 48' 756 365 170 $C.PaleYellow $C.Orange
    Set-Notes $s 'The grouped test macro F1 is 0.979 and test accuracy is also 0.979, with one medication-history document classified as a prior-authorization request. The harder challenge set drops to 0.916 macro F1 and contains four failures. Batch inference latency is approximately 0.004 milliseconds per document on CPU in the report. These are synthetic-corpus results, not production claims.'

    # 11 — Calibration
    $s = New-Slide '10  /  EVALUATION' 'Accuracy is not the same as confidence quality' 'Calibration and review-routing reveal the next engineering priority'
    Add-Card $s 'Test accuracy' 'Correct class prediction on the grouped held-out test set.' 55 155 190 130 $C.Green '97.9%'
    Add-Card $s 'Mean confidence' 'Average maximum probability despite strong accuracy.' 270 155 190 130 $C.Blue '56.1%'
    Add-Card $s 'Calibration error' '10-bin expected calibration error; lower is better.' 485 155 190 130 $C.Red '0.418'
    Add-Card $s 'Review routing' 'Predictions below the 0.65 threshold.' 700 155 190 130 $C.Orange '68.8%'
    Add-Rect $s 55 325 835 115 $C.PaleYellow 8 | Out-Null
    Add-Text $s 'Engineering interpretation' 78 345 250 22 13 $C.Orange $true | Out-Null
    Add-Text $s 'The classifier is often correct but under-confident. Probability calibration and threshold selection matter more next than chasing a higher headline accuracy.' 78 378 780 48 18 $C.Navy $true | Out-Null
    Set-Notes $s 'This slide is the most important evaluation lesson. Accuracy alone would suggest the model is nearly finished, but mean confidence is only 0.561, expected calibration error is 0.418, and 68.8 percent of test and challenge documents route to review. The next step should include calibration analysis and threshold tradeoffs, not simply a more complex model.'

    # 12 — Matching
    $s = New-Slide '11  /  CORE INTELLIGENCE' 'Criteria-to-evidence matching uses a five-state contract' 'Deterministic rules first; ambiguity is represented instead of guessed'
    $states = @(
        @('SATISFIED',$C.Green,$C.PaleGreen), @('NOT SATISFIED',$C.Red,$C.PaleRed),
        @('MISSING',$C.Orange,$C.PaleYellow), @('AMBIGUOUS',$C.Purple,$C.PaleBlue),
        @('HUMAN REVIEW',$C.Blue,$C.PaleBlue)
    )
    for ($i = 0; $i -lt $states.Count; $i++) {
        Add-Pill $s $states[$i][0] (55 + $i * 170) 150 150 $states[$i][2] $states[$i][1]
    }
    Add-Rect $s 55 210 850 220 $C.White 8 $C.Cloud | Out-Null
    Add-Text $s 'Policy rule' 78 235 130 20 11 $C.Mid $true | Out-Null
    Add-Text $s 'At least 12 weeks of Drug A' 78 263 250 28 18 $C.Navy $true | Out-Null
    Add-Text $s 'Structured criterion' 365 235 150 20 11 $C.Mid $true | Out-Null
    Add-Text $s "operator:  >=`nexpected:  12 weeks" 365 263 190 58 16 $C.Blue $true | Out-Null
    Add-Text $s 'Evidence' 620 235 120 20 11 $C.Mid $true | Out-Null
    Add-Text $s 'Drug A used for 16 weeks' 620 263 230 28 18 $C.Navy $true | Out-Null
    Add-Line $s 290 293 350 293 $C.Blue2 2 $true | Out-Null
    Add-Line $s 552 293 608 293 $C.Blue2 2 $true | Out-Null
    Add-Rect $s 248 350 465 52 $C.PaleGreen 8 | Out-Null
    Add-Text $s '16 >= 12  →  SATISFIED  •  deterministic  •  confidence 0.93' 268 366 425 22 14 $C.Green $true 2 | Out-Null
    Set-Notes $s 'Matching is the core intelligence layer. It finds typed evidence, rejects low-confidence extraction, checks unit compatibility, performs explicit numeric and outcome comparisons in Python, and returns one of five states. If evidence says several months where a number is required, the result is ambiguous and routes to a reviewer instead of being converted into a fabricated duration.'

    # 13 — Demo case
    $s = New-Slide '12  /  END-TO-END DEMO' 'One synthetic case, six policy criteria' 'PA-DEMO-001 proves the workflow spine and reviewer-ready traceability'
    Add-Rect $s 55 155 560 52 $C.Green 6 | Out-Null
    Add-Rect $s 615 155 140 52 $C.Orange 0 | Out-Null
    Add-Rect $s 755 155 140 52 $C.Purple 6 | Out-Null
    Add-Text $s '4 SATISFIED' 65 171 540 20 14 $C.White $true 2 | Out-Null
    Add-Text $s '1 MISSING' 625 171 120 20 13 $C.White $true 2 | Out-Null
    Add-Text $s '1 AMBIGUOUS' 765 171 120 20 13 $C.White $true 2 | Out-Null
    $rows = @(
        @('C1','Diagnosis documented','SATISFIED','clinical_note.pdf p.1'),
        @('C2','≥ 12 weeks prior therapy','SATISFIED','medication_history.pdf p.2'),
        @('C3','Inadequate response','SATISFIED','medication_history.pdf p.2'),
        @('C4','A1c below 8.0','SATISFIED','lab_report.pdf p.1'),
        @('C5','Screening document','MISSING','No evidence found'),
        @('C6','Recent course duration','AMBIGUOUS','“several months”')
    )
    $table = $s.Shapes.AddTable(7, 4, 55, 235, 840, 210).Table
    $headers = @('ID','Policy requirement','Result','Patient evidence')
    for ($cidx = 1; $cidx -le 4; $cidx++) {
        $cell = $table.Cell(1,$cidx).Shape
        $cell.Fill.ForeColor.RGB = $C.Navy
        $cell.TextFrame.TextRange.Text = $headers[$cidx - 1]
        $cell.TextFrame.TextRange.Font.Name = 'Aptos'; $cell.TextFrame.TextRange.Font.Size = 11
        $cell.TextFrame.TextRange.Font.Bold = -1; $cell.TextFrame.TextRange.Font.Color.RGB = $C.White
    }
    for ($ridx = 0; $ridx -lt $rows.Count; $ridx++) {
        for ($cidx = 0; $cidx -lt 4; $cidx++) {
            $cell = $table.Cell($ridx + 2,$cidx + 1).Shape
            $cell.Fill.ForeColor.RGB = $(if ($ridx % 2 -eq 0) { $C.White } else { $C.Snow })
            $cell.TextFrame.TextRange.Text = $rows[$ridx][$cidx]
            $cell.TextFrame.TextRange.Font.Name = 'Aptos'; $cell.TextFrame.TextRange.Font.Size = 10.5
            $cell.TextFrame.TextRange.Font.Color.RGB = $(if ($cidx -eq 2 -and $ridx -lt 4) { $C.Green } elseif ($cidx -eq 2 -and $ridx -eq 4) { $C.Orange } elseif ($cidx -eq 2) { $C.Purple } else { $C.Ink })
            if ($cidx -eq 2) { $cell.TextFrame.TextRange.Font.Bold = -1 }
        }
    }
    Add-Pill $s 'GROUNDEDNESS GATE: PASS' 348 462 255 $C.PaleGreen $C.Green
    Set-Notes $s 'The demo case has five detected documents and six policy criteria. Four are supported, one required screening document is missing, and one duration is ambiguous because the source says several months. Every supported criterion includes the patient document, page, source span, policy page, method, and confidence. The structural groundedness gate passes.'

    # 14 — Groundedness
    $s = New-Slide '13  /  TRUSTWORTHY AI' 'Groundedness gate before reviewer display' 'No supported claim may exist without patient evidence and a policy source'
    Add-FlowBox $s 'Criterion evaluation' 65 190 170 62 $C.White $C.Ink
    Add-Line $s 235 221 300 221 $C.Blue2 2 $true | Out-Null
    Add-FlowBox $s 'Policy source present?' 300 180 170 82 $C.PaleYellow $C.Orange $C.Orange
    Add-Line $s 470 221 535 221 $C.Blue2 2 $true | Out-Null
    Add-FlowBox $s "Patient source present`nwhen support is claimed?" 535 180 190 82 $C.PaleYellow $C.Orange $C.Orange
    Add-Line $s 725 221 790 221 $C.Blue2 2 $true | Out-Null
    Add-FlowBox $s 'PASS / FAIL' 790 190 120 62 $C.PaleGreen $C.Green $C.Green
    Add-Card $s 'Current gate' 'Structural groundedness: validates evidence IDs, patient provenance, and policy provenance for each result.' 65 315 250 125 $C.Green
    Add-Card $s 'Future gate' 'Semantic claim verification for generated text, citation correctness, conflicting evidence, and unsupported-claim rate.' 355 315 250 125 $C.Purple
    Add-Card $s 'Human control' 'Missing or ambiguous data becomes a visible review task. No autonomous submission is allowed.' 645 315 250 125 $C.Blue
    Set-Notes $s 'Today, the groundedness gate verifies the structural contract. Supported or not-satisfied claims must cite patient evidence, and every evaluation must retain its policy source. When generated drafting is added, the same gate interface can expand to semantic faithfulness checks, citation correctness, conflict detection, and unsupported-claim scoring.'

    # 15 — Engineering quality
    $s = New-Slide '14  /  ENGINEERING DISCIPLINE' 'Reproducible, testable, and honest by design' 'The repository is organized as an installable Python 3.12 package'
    Add-Card $s 'Automated tests' 'Pipeline states, policy mismatches, unit conflicts, classifier persistence, split isolation, rendering, and ingestion.' 55 155 250 140 $C.Green '21 PASS'
    Add-Card $s 'Reproducibility' 'Locked environment, deterministic seed, generated manifests, CLI entry points, and versionable report artifacts.' 355 155 250 140 $C.Blue 'seed 42'
    Add-Card $s 'Evaluation artifacts' 'Classifier report, ingestion report, structured end-to-end case JSON, and persisted trusted model bundle.' 655 155 250 140 $C.Purple '3 reports'
    Add-BulletList $s @('Vectorizer fit on training split only; challenge set excluded from model selection','Errors and calibration are reported alongside headline performance','Synthetic-only scope and unmeasured OCR accuracy are stated explicitly') 70 340 805 15.5 48 $C.Blue
    Set-Notes $s 'A course project should demonstrate engineering practice, not only a notebook result. The repository includes CLI commands, locked dependencies, deterministic dataset generation, persisted artifacts, typed models, and 21 passing tests in the current run. Reports include error cases and calibration. The project also documents limitations, including synthetic-only validity and unavailable OCR accuracy.'

    # 16 — IBM mapping
    $s = New-Slide '15  /  COURSE ALIGNMENT' 'How RxAuth AI maps to IBM AI Engineering learning goals' 'Each course block unlocks a concrete project capability and measurable deliverable'
    $map = @(
        @('Python / data','Typed ingestion, preprocessing, manifests','Implemented'),
        @('Machine Learning','TF-IDF + logistic regression benchmark','Implemented'),
        @('Deep Learning','Transformer classifier vs. same benchmark','Next'),
        @('Computer Vision / NLP','OCR and evidence extraction with confidence','Partial / next'),
        @('LLM applications + RAG','Public payer-policy retrieval and criteria extraction','Roadmap'),
        @('AI agents','Controlled state graph and review checkpoints','Roadmap'),
        @('Model evaluation','F1, calibration, routing, latency, groundedness','Implemented / expands')
    )
    $table = $s.Shapes.AddTable(8, 3, 50, 145, 860, 320).Table
    $headers = @('IBM learning block','RxAuth AI deliverable','Status')
    for ($cidx = 1; $cidx -le 3; $cidx++) {
        $cell = $table.Cell(1,$cidx).Shape; $cell.Fill.ForeColor.RGB = $C.Navy
        $cell.TextFrame.TextRange.Text = $headers[$cidx - 1]; $cell.TextFrame.TextRange.Font.Name = 'Aptos'
        $cell.TextFrame.TextRange.Font.Size = 11; $cell.TextFrame.TextRange.Font.Bold = -1; $cell.TextFrame.TextRange.Font.Color.RGB = $C.White
    }
    for ($ridx = 0; $ridx -lt $map.Count; $ridx++) {
        for ($cidx = 0; $cidx -lt 3; $cidx++) {
            $cell = $table.Cell($ridx + 2,$cidx + 1).Shape
            $cell.Fill.ForeColor.RGB = $(if ($ridx % 2 -eq 0) { $C.White } else { $C.Snow })
            $cell.TextFrame.TextRange.Text = $map[$ridx][$cidx]; $cell.TextFrame.TextRange.Font.Name = 'Aptos'; $cell.TextFrame.TextRange.Font.Size = 10.5
            $cell.TextFrame.TextRange.Font.Color.RGB = $(if ($cidx -eq 2 -and $map[$ridx][$cidx] -eq 'Implemented') { $C.Green } elseif ($cidx -eq 2) { $C.Orange } else { $C.Ink })
            if ($cidx -eq 2) { $cell.TextFrame.TextRange.Font.Bold = -1 }
        }
    }
    Set-Notes $s 'This is not a disconnected set of course exercises. Python and data engineering produce the ingestion layer. Classical machine learning produces the baseline. Deep learning will be evaluated against the same data contract. Computer vision and NLP support extraction. RAG will retrieve public policy, and an explicit state graph will orchestrate controlled stages. Evaluation remains first-class at every layer.'

    # 17 — Roadmap
    $s = New-Slide '16  /  ROADMAP' 'From Phase 1.5 to a portfolio-ready V1' 'Advance only when each layer has a defensible evaluation boundary'
    Add-Line $s 90 260 875 260 $C.Cloud 5 | Out-Null
    $phases = @(
        @('M0','Workflow spine','Done',$C.Green),
        @('P1','ML baseline','Done',$C.Green),
        @('P1.5','Ingestion + hardening','Current',$C.Blue),
        @('P2','Transformer comparison','Next',$C.Orange),
        @('P3','Extraction + policy RAG','Roadmap',$C.Purple),
        @('V1','Controlled agent + UI','Roadmap',$C.Navy)
    )
    for ($i = 0; $i -lt $phases.Count; $i++) {
        $x = 90 + ($i * 155)
        $node = $s.Shapes.AddShape(9, $x, 237, 46, 46); $node.Fill.Solid(); $node.Fill.ForeColor.RGB = $phases[$i][3]; $node.Line.Visible = 0
        Add-Text $s $phases[$i][0] ($x - 4) 250 54 16 9 $C.White $true 2 | Out-Null
        Add-Text $s $phases[$i][1] ($x - 46) 300 140 45 12 $C.Ink $true 2 | Out-Null
        Add-Pill $s $phases[$i][2] ($x - 20) 365 86 $(if ($phases[$i][2] -eq 'Done') { $C.PaleGreen } elseif ($phases[$i][2] -eq 'Current') { $C.PaleBlue } else { $C.PaleYellow }) $(if ($phases[$i][2] -eq 'Done') { $C.Green } elseif ($phases[$i][2] -eq 'Current') { $C.Blue } else { $C.Orange })
    }
    Add-Text $s 'Model selection rule: same splits, same metrics, same reporting contract—then compare quality, calibration, latency, size, and deployment cost.' 80 430 810 42 15 $C.Navy $true 2 | Out-Null
    Set-Notes $s 'The immediate next step is a small transformer classifier trained on the exact same split and evaluated with the same contract. It should be compared on macro F1, robustness, calibration, review routing, latency, and artifact size. Later phases add confidence-aware extraction, public payer-policy retrieval, criteria extraction, controlled generation, and a reviewer interface.'

    # 18 — Conclusion
    $s = New-Slide '17  /  CONCLUSION' 'What this project demonstrates' 'A course project can be technically rigorous, domain-aware, and honest about its limits'
    Add-Card $s '1  End-to-end thinking' 'Data contracts, preprocessing, training, evaluation, inference, decision logic, trust gates, and human review form one coherent system.' 55 155 250 210 $C.Blue
    Add-Card $s '2  Evaluation maturity' 'Grouped splits, challenge data, calibration, review routing, latency, failure cases, and explicit non-claims show scientific discipline.' 355 155 250 210 $C.Green
    Add-Card $s '3  Responsible design' 'Synthetic data, administrative scope, source provenance, deterministic rules, and no autonomous submission reduce avoidable risk.' 655 155 250 210 $C.Purple
    Add-Rect $s 155 405 650 58 $C.Navy 8 | Out-Null
    Add-Text $s 'Build an AI system that can prove where every important answer came from.' 180 422 600 24 17 $C.White $true 2 | Out-Null
    Add-Text $s 'Questions?' 785 60 120 32 16 $C.Blue $true 3 | Out-Null
    Set-Notes $s 'The takeaway is not that a synthetic baseline solves prior authorization. The achievement is a defensible engineering foundation: typed data, leakage-resistant experimentation, explicit uncertainty, deterministic business logic, source grounding, human review, and reproducible reports. That foundation makes later deep learning and generative AI work measurable rather than decorative.'

    # 19 — Appendix: demo
    $s = New-Slide 'APPENDIX A' 'Reproduce the demo and benchmarks' 'All commands run from the repository root'
    Add-Rect $s 55 150 850 245 $C.Navy 8 | Out-Null
    $commands = @(
        'uv sync --group dev',
        'uv run rxauth-build-dataset',
        'uv run rxauth-benchmark-ingestion',
        'uv run rxauth-train-classifier',
        'uv run rxauth-milestone0',
        'uv run pytest'
    ) -join "`r`n"
    Add-Text $s $commands 85 177 785 190 18 $C.White $false 1 'Cascadia Mono' | Out-Null
    Add-Pill $s 'Expected: 21 tests pass' 55 425 190 $C.PaleGreen $C.Green
    Add-Pill $s 'Outputs: reports/*.md + case JSON' 270 425 265 $C.PaleBlue $C.Blue
    Add-Pill $s 'No real PHI' 560 425 130 $C.PaleYellow $C.Orange
    Set-Notes $s 'These commands rebuild the synthetic datasets, run ingestion and classifier benchmarks, execute the end-to-end case, and run the tests. The primary artifacts are reports/classifier_baseline.md, reports/ingestion_benchmark.md, and reports/case_PA-DEMO-001.json.'

    # 20 — Appendix: source map
    $s = New-Slide 'APPENDIX B' 'Evidence behind this presentation' 'Every metric and design claim maps to a repository artifact'
    Add-Card $s 'README.md' 'Project thesis, scope, architecture, IBM course mapping, roadmap, and security posture.' 55 150 250 130 $C.Blue
    Add-Card $s 'docs/phase-1.5.md' 'Typed ingestion contract, grouped split design, current outcomes, and next step.' 355 150 250 130 $C.Cyan
    Add-Card $s 'reports/classifier_baseline.md' 'Accuracy, macro F1, confidence, calibration, routing, latency, confusion matrix, failures.' 655 150 250 130 $C.Green
    Add-Card $s 'reports/ingestion_benchmark.md' 'Rendered corpus size, PDF character error rate, preprocessing success, OCR non-claim.' 55 315 250 130 $C.Orange
    Add-Card $s 'reports/case_PA-DEMO-001.json' 'Six criteria, statuses, explanations, confidence, policy source, and patient evidence.' 355 315 250 130 $C.Purple
    Add-Card $s 'tests/' 'Executable checks for matching behavior, ingestion, dataset contracts, and classifier persistence.' 655 315 250 130 $C.Red
    Set-Notes $s 'This appendix provides the audit trail for the presentation. The deck intentionally uses only repository-backed metrics and labels future capabilities as roadmap items. This source map makes it easy for an instructor to verify each technical claim.'

    $script:Deck.SaveAs($pptPath, 24)
    $script:Deck.SaveAs($pdfPath, 32)
    Write-Output "Created: $pptPath"
    Write-Output "Created: $pdfPath"
    Write-Output "Slides: $($script:Deck.Slides.Count)"
}
finally {
    if ($null -ne $script:Deck) { $script:Deck.Close() }
    if ($null -ne $powerPoint) { $powerPoint.Quit() }
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($script:Deck) | Out-Null
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($powerPoint) | Out-Null
    [gc]::Collect(); [gc]::WaitForPendingFinalizers()
}
