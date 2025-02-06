import re
from typing import List


from zavod import Context
from zavod import helpers as h

NULL_CITIZENSHIPS = ["NIEUSTALONE", "BEZPAŃSTWOWIEC"]
CITIZENSHIPS_WITH_SPACES = [
    "WIELKA BRYTANIA",
    "SRI LANKA",
    "ARABIA SAUDYJSKA",
    "NOWA ZELANDIA",
    "BOŚNIA I HERCEGOWINA",
    "ZJEDNOCZONE EMIRATY ARABSKIE",
    "KOREAŃSKA REPUBLIKA LUDOWO-DEMOKRATYCZNA",
    "WYBRZEŻE KOŚCI SŁONIOWEJ",
    "DEMOKRATYCZNA REPUBLIKA KONGA",
    "SIERRA LEONE",
    "KOREA POŁUDNIOWA",
]


def clean_and_split_citizenship(citizenship: str) -> str | List[str]:
    """
    Try to split the space-delimited citizenship field in various creative ways

    Args:
        citizenship: The string content of the citizenship field

    Returns:
        A string or a list of string containing the cleaned citizenship(s).

    """
    res = []
    # Sometimes, we have another spelling of the country in brackets, like "USA (STANY ZJEDNOCZONE AMERYKI)"
    cleaned_citizenship = re.sub(r" \(.+\)", "", citizenship)
    # Split citizenships with spaces out before naively splitting on space
    for c in CITIZENSHIPS_WITH_SPACES:
        if c in cleaned_citizenship:
            cleaned_citizenship = cleaned_citizenship.replace(c, "")
            res.append(c)

    res += cleaned_citizenship.split(" ")

    return res


def crawl_person(context: Context, url: str):
    context.log.debug(f"Crawling person page {url}")

    doc = context.fetch_html(url, cache_days=7)
    # Extract details using XPath based on the provided HTML structure
    details = {
        "full_name": "//div[@class='head']/h2/text()",
        "middle_name": "//p[contains(text(), 'Data urodzenia:')]/strong/text()",
        "father_name": "//p[contains(text(), 'Imię ojca:')]/strong/text()",
        "mother_name": "//p[contains(text(), 'Imię matki:')]/strong/text()",
        "mother_maiden_name": "//p[contains(text(), 'Nazwisko panieńskie matki:')]/strong/text()",
        "gender": "//p[contains(text(), 'Płeć:')]/strong/text()",
        "birth_place": "//p[contains(text(), 'Miejsce urodzenia:')]/strong/text()",
        "birth_date": "//p[contains(text(), 'Data urodzenia:')]/strong/text()",
        "alias": "//p[contains(text(), 'Pseudonim:')]/strong/text()",
        "citizenship": "//p[contains(text(), 'Obywatelstwo:')]/strong/text()",
        "height": "//p[contains(text(), 'Wzrost:')]/strong/text()",
        "eye_color": "//p[contains(text(), 'Kolor oczu:')]/strong/text()",
    }
    info = dict()
    for key, xpath in details.items():
        q = doc.xpath(xpath)
        if q:
            text = q[0].strip()
            if text != "-":
                info[key] = text

    person = context.make("Person")
    person.id = context.make_slug(info.get("full_name"), info.get("birth_date"))
    person.add("sourceUrl", url)
    person.add("topics", "crime")
    person.add("topics", "wanted")
    person.add("country", "pl")

    h.apply_name(
        person, full=info.pop("full_name"), middle_name=info.pop("middle_name", None)
    )
    h.apply_date(person, "birthDate", info.pop("birth_date", None))
    person.add("birthPlace", info.pop("birth_place", None))
    person.add("gender", info.pop("gender", None))
    person.add("alias", info.pop("alias", None))
    person.add("fatherName", info.pop("father_name", None))
    person.add("motherName", info.pop("mother_name", None))
    person.add("motherName", info.pop("mother_maiden_name", None))
    person.add("height", info.pop("height", None))
    person.add("eyeColor", info.pop("eye_color", None))

    citizenship = info.pop("citizenship", None)
    cleaned_citizenships = (
        clean_and_split_citizenship(citizenship) if citizenship else None
    )
    person.add("citizenship", cleaned_citizenships, original_value=citizenship)

    crimes = doc.xpath(
        "//p[contains(text(), 'Podstawy poszukiwań:')]/following-sibling::ul//a/text()"
    )
    if not crimes:
        context.log.warn("No crimes found for person", entity_id=person.id, url=url)
    person.add("notes", crimes)

    context.audit_data(info)

    context.emit(person)


def crawl_index(context, url) -> str | None:
    context.log.info(f"Crawling index page {url}")
    doc = context.fetch_html(url, cache_days=1)
    # makes it easier to extract dedicated details page
    doc.make_links_absolute(context.dataset.data.url)
    cells = doc.xpath("//li[.//a[contains(@href, '/pos/form/r')]]/a/@href")
    for cell in cells:
        crawl_person(context, cell)

    # On the last page, the next button will not have an <a>, so this will not match
    next_button_href = doc.xpath(
        "//li/a/span[contains(text(), 'następna')]/parent::a/@href"
    )
    return next_button_href[0] if next_button_href else None


def crawl(context):
    next_url = context.dataset.data.url
    # Use this construction instead of recursion because Python sets a recursion limit
    while next_url:
        next_url = crawl_index(context, next_url)
