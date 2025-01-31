import io
from csv import DictReader
from typing import List, Dict, Optional, Tuple, Set
from zipfile import ZipFile, BadZipFile
from zavod import Context, Entity, helpers as h
from zavod.logic.pep import categorise
from zavod.shed.trans import (
    apply_translit_full_name,
    make_position_translation_prompt,
)

TRANSLIT_OUTPUT = {
    "eng": ("Latin", "English"),
}
POSITION_PROMPT = prompt = make_position_translation_prompt("cnr")


def extract_latest_filing(
    dates: List[Dict[str, object]],
) -> Tuple[Optional[str], Optional[int]]:
    if not dates:
        return None, None

    # Iterate over all entries in reverse order
    for entry in reversed(dates):
        report_id = entry.get("stariIzvjestaj")
        if report_id != -1:  # Check for the first valid report
            return entry.get("datum"), report_id

    # If no valid entry is found
    return None, None


def fetch_csv(context: Context, report_id: int, file_pattern: str) -> List[Dict]:
    """Generic CSV fetcher to retrieve CSV rows based on the file pattern."""
    url = f"https://portal.antikorupcija.me:9343/acamPublic/izvestajDetailsCSV.json?izvestajId={report_id}"
    zip_path = context.fetch_resource(f"{report_id}.zip", url)

    try:
        with ZipFile(zip_path, "r") as zip:
            filtered_names = [
                name for name in zip.namelist() if name.startswith(file_pattern)
            ]
            file_name = next((name for name in filtered_names), None)
            if not file_name:
                context.log.warning(
                    "No matching file found in the ZIP archive.", file_pattern
                )
                return []

            with zip.open(file_name) as zfh:
                fh = io.TextIOWrapper(zfh, encoding="utf-8-sig")
                reader = DictReader(fh, delimiter=",", quotechar='"')
                return list(reader)
    except BadZipFile as e:
        context.log.info(
            "Failed to open ZIP file. Skipping", path=zip_path, exception_str=str(e)
        )
        return []


def family_row_entity(context: Context, row) -> Entity:
    first_name = row.pop("IME_CLANA_PORODICE")
    last_name = row.pop("PREZIME_CLANA_PORODICE")
    maiden_name = row.pop("RODJENO_PREZIME_CLANA_PORODICE")
    city = row.pop("MESTO")
    entity = context.make("Person")
    entity.id = context.make_id(first_name, last_name, city)
    entity.add("citizenship", row.pop("DRZAVLJANSTVO"))
    h.apply_name(
        entity, first_name=first_name, last_name=last_name, maiden_name=maiden_name
    )
    address = h.make_address(context, city=city, place=row.pop("BORAVISTE"), lang="cnr")
    h.copy_address(entity, address)
    context.audit_data(row, ignore=["FUNKCIONER_IME", "FUNKCIONER_PREZIME"])
    return entity


def crawl_relative(context, person_entity, relatives):
    for row in relatives:
        relationship = row.pop("SRODSTVO")
        relative = family_row_entity(context, row)
        if len(relative.caption) < 4:
            return
        context.emit(relative)

        # Emit a relationship
        rel = context.make("Family")
        rel.id = context.make_id(person_entity.id, relationship, relative.id)
        rel.add("relationship", relationship)
        rel.add("person", person_entity.id)
        rel.add("relative", relative.id)

        context.emit(rel)


def make_affiliation_entities(
    context: Context, entity: Entity, function, row: dict, filing_date, report_id
) -> Tuple[List[Entity], Set[str]]:  # List[Entity]:
    """Creates Position and Occupancy provided that the Occupancy meets OpenSanctions criteria.
    * A position's name include the title and the name of the legal entity
    * All positions (and Occupancies, Persons) are assumed to be Montenegrin
    """

    organization = row.pop("ORGANIZACIJA")
    start_date = row.pop("DATUM_POCETKA_OBAVLJANJA", None)
    end_date = row.pop("DATUM_PRESTANKA_OBAVLJNJA")
    context.audit_data(
        row,
        ignore=[
            "ORGANIZACIJA_IMENOVANJA",
            "ORGANIZACIJA_SAGLASNOSTI",
            "FUNKCIONER_IME",
            "FUNKCIONER_PREZIME",
        ],
    )

    position_name = f"{function}, {organization}"
    entity.add("position", position_name)

    position = h.make_position(
        context,
        position_name,
        # topics=["gov.national"],  # for testing
        country="ME",
    )
    apply_translit_full_name(
        context, position, "cnr", position_name, TRANSLIT_OUTPUT, POSITION_PROMPT
    )

    categorisation = categorise(context, position, is_pep=True)
    categorisation_topics = set(categorisation.topics)

    occupancy = h.make_occupancy(
        context,
        entity,
        position,
        no_end_implies_current=True,
        categorisation=categorisation,
        propagate_country=True,
        start_date=start_date,
        end_date=end_date,
    )
    entities = []
    if occupancy:
        # Switch to declarationDate, once it's introduced in FtM
        occupancy.add("date", filing_date)
        entities.extend([position, occupancy])
    return entities, categorisation_topics


# Verified Jan 2025 that when SRODSTVO == Funkcioner,
# FUNKCIONER_IME roughly equals IME_CLANA_PORODICE (first name)
# and FUNKCIONER_PREZIME roughly equals PREZIME_CLANA_PORODICE (last name)
def split_family(rows) -> Tuple[List[Dict[str, str]], Optional[Dict[str, str]]]:
    officials = [row for row in rows if row.get("SRODSTVO") == "Funkcioner"]
    relatives = [row for row in rows if row.get("SRODSTVO") != "Funkcioner"]
    assert len(officials) <= 1, "Multiple PEPs found in the relatives CSV."
    return relatives, officials[0] if officials else None


def crawl_person(context: Context, person):
    full_name = person.pop("imeIPrezime")
    dates = person.pop("izvjestajImovine")
    function_label = person.pop("nazivFunkcije")
    filing_date, report_id = extract_latest_filing(dates)
    if report_id:
        relatives_rows = fetch_csv(context, report_id, "csv_clanovi_porodice_")
        function_rows = fetch_csv(context, report_id, "csv_funkcije_funkcionera_")
    else:
        relatives_rows = []
        function_rows = []
    relatives_rows, official = split_family(relatives_rows)

    if official:
        official.pop("SRODSTVO")
        entity = family_row_entity(context, official)
    else:
        entity = context.make("Person")
        entity.id = context.make_id(full_name, function_label)
        h.apply_name(entity, full_name)

    categorisation_topics = set()
    position_entities = []
    if function_rows:
        for row in function_rows:
            function = row.pop("FUNKCIJA")
            entities, topics = make_affiliation_entities(
                context, entity, function, row, filing_date, report_id
            )
            position_entities.extend(entities)
            categorisation_topics.update(topics)
    else:
        position = h.make_position(context, function_label, country="ME")
        categorisation = categorise(context, position, is_pep=True)
        categorisation_topics.update(categorisation.topics)
        position_entities.append(position)

    if "gov.national" in categorisation_topics:
        crawl_relative(context, entity, relatives_rows)

    if position_entities:
        for position in position_entities:
            context.emit(position)
        context.emit(entity)

    # True if we got some valid rows from the Zip file
    return relatives_rows or function_rows


def crawl(context: Context):
    valid_zips = 0
    page = 0
    max_pages = 1200
    while True:
        data_url = f"https://obsidian.antikorupcija.me/api/ask-interni-pretraga/ank-izvjestaj-imovine/pretraga-izvjestaj-imovine-javni?page={page}&size=20"
        doc = context.fetch_json(data_url.format(page=page))

        if not doc:  # Break if an empty list is returned
            context.log.info(f"Stopped at page {page}")
            break

        for person in doc:
            if crawl_person(context, person):
                valid_zips += 1

        page += 1
        if page >= max_pages:
            context.log.warning(
                f"Emergency exit: Reached the maximum page limit of {max_pages}."
            )
            break

        if not valid_zips:
            context.log.warning("No valid ZIP files found.")
