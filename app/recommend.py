"""Rule-based personalised recommendations (FR-10). Uses the risk level and the
user's own parameters, including lifestyle fields that are not model inputs."""


def recommendations(level, d):
    r = []

    def add(cat, text, prio="medium"):
        r.append({"category": cat, "text": text, "priority": prio})

    g, bmi, age = d.get("Glucose"), d.get("BMI"), d.get("Age")
    sys_, dia = d.get("Systolic"), d.get("BloodPressure")
    if level == "High Risk":
        add("Medical", "Book an appointment with a doctor soon for an HbA1c / fasting plasma glucose "
            "test to confirm your status. This tool is a screening aid, not a diagnosis.", "high")
    elif level == "Moderate Risk":
        add("Medical", "Discuss your result with a healthcare professional and plan a glucose "
            "screening within the next 3 months.", "high")
    else:
        add("Medical", "Keep up routine check-ups and repeat this assessment every 6-12 months.", "low")

    if g is not None:
        if g >= 126:
            add("Glucose", f"Your fasting glucose ({g:g} mg/dL) is in the diabetic range (>=126). "
                "Seek medical confirmation promptly.", "high")
        elif g >= 100:
            add("Glucose", f"Your fasting glucose ({g:g} mg/dL) is in the pre-diabetic range (100-125). "
                "Reduce refined sugar and recheck in 3 months.", "high")
    if bmi is not None:
        if bmi >= 30:
            add("Weight", f"BMI {bmi:.1f} is in the obese range. Losing 5-7% of body weight "
                "markedly lowers type-2 diabetes risk.", "high")
        elif bmi >= 25:
            add("Weight", f"BMI {bmi:.1f} is above the healthy range (18.5-24.9). Aim for gradual, "
                "sustainable weight loss.", "medium")
    if (sys_ and sys_ >= 130) or (dia and dia >= 80):
        add("Blood pressure", "Your blood pressure is elevated. Reduce salt, limit alcohol and have it "
            "checked regularly.", "medium")
    act = d.get("Activity")
    if act is not None and act < 2.5:
        add("Activity", "Aim for at least 150 minutes (2.5 h) of moderate exercise per week, e.g. "
            "brisk walking 30 min on 5 days.", "medium")
    diet = d.get("Diet")
    if diet == "Poor":
        add("Diet", "Move towards vegetables, whole grains, lean protein and legumes; cut sugary drinks, "
            "fried food and processed snacks.", "medium")
    elif diet == "Average":
        add("Diet", "Improve portion control and swap white rice/bread for whole-grain options.", "low")
    if d.get("Smoking") in ("Current", "Yes"):
        add("Smoking", "Smoking raises diabetes and heart-disease risk. Ask your doctor about "
            "cessation support.", "medium")
    if d.get("FamilyHistory") == "Yes":
        add("Family history", "A family history raises your baseline risk - screening should start "
            "earlier and be more regular.", "medium")
    if age is not None and age >= 45:
        add("Age", "Risk rises with age; adults 45+ should be screened at least every 3 years.", "low")
    add("Lifestyle", "Sleep 7-8 hours, manage stress and stay hydrated - these support healthy glucose control.", "low")
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(r, key=lambda x: order[x["priority"]])
