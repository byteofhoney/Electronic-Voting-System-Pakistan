# Maps first 2 digits of CNIC → (division_name, province_name, province_id)
# province_id matches your DB: Punjab=1, Sindh=2, Balochistan=3, KPK=4, Islamabad=5

CNIC_PREFIX_MAP = {
    # ── Khyber Pakhtunkhwa ──────────────────────────────────
    '11': ('Bannu Division',            'Khyber Pakhtunkhwa', 4),
    '12': ('Dera Ismail Khan Division', 'Khyber Pakhtunkhwa', 4),
    '13': ('Hazara Division',           'Khyber Pakhtunkhwa', 4),
    '14': ('Kohat Division',            'Khyber Pakhtunkhwa', 4),
    '15': ('Malakand Division',         'Khyber Pakhtunkhwa', 4),
    '16': ('Mardan Division',           'Khyber Pakhtunkhwa', 4),
    '17': ('Peshawar Division',         'Khyber Pakhtunkhwa', 4),

    # ── Punjab ──────────────────────────────────────────────
    '31': ('Bahawalpur Division',       'Punjab', 1),
    '32': ('Dera Ghazi Khan Division',  'Punjab', 1),
    '33': ('Faisalabad Division',       'Punjab', 1),
    '34': ('Gujranwala / Gujrat',       'Punjab', 1),
    '35': ('Lahore Division',           'Punjab', 1),
    '36': ('Multan / Sahiwal Division', 'Punjab', 1),
    '37': ('Rawalpindi Division',       'Punjab', 1),
    '38': ('Sargodha / Mianwali',       'Punjab', 1),

    # ── Sindh ───────────────────────────────────────────────
    '41': ('Hyderabad Division',        'Sindh',  2),
    '42': ('Karachi Division',          'Sindh',  2),
    '43': ('Larkana Division',          'Sindh',  2),
    '44': ('Mirpur Khas Division',      'Sindh',  2),
    '45': ('Sukkur / Shaheed BB Div.',  'Sindh',  2),

    # ── Balochistan ─────────────────────────────────────────
    '51': ('Kalat / Rakhshan Division', 'Balochistan', 3),
    '52': ('Makran Division',           'Balochistan', 3),
    '53': ('Nasirabad Division',        'Balochistan', 3),
    '54': ('Quetta Division',           'Balochistan', 3),
    '55': ('Sibi Division',             'Balochistan', 3),
    '56': ('Zhob / Loralai Division',   'Balochistan', 3),

    # ── Islamabad Capital Territory ─────────────────────────
    '61': ('Islamabad','Islamabad', 5),
}


def get_province_from_cnic(cnic: str):
    """
    Takes a formatted CNIC string like '37201-1234567-1'
    Returns (division_name, province_name, province_id) or None if unknown.
    """
    # Strip dashes and grab first 2 digits
    digits = cnic.replace('-', '')
    if len(digits) < 13:
        return None
    prefix = digits[:2]
    return CNIC_PREFIX_MAP.get(prefix)


def validate_cnic_format(cnic: str) -> bool:
    """Returns True if CNIC matches XXXXX-XXXXXXX-X format."""
    import re
    return bool(re.match(r'^\d{5}-\d{7}-\d$', cnic))