import csv
from typing import Dict
from zavod import Context, helpers as h
from typing import List


DATE_FORMATS = ["%m/%d/%Y", "%Y"]


def crawl_row(context: Context, row: Dict[str, str]):
    full_name = row.pop("name")
    other_name = h.multi_split(row.pop("other name"), [",", ";"])
    birth_date_1 = h.parse_date(row.pop("date of birth") or None, DATE_FORMATS)
    birth_date_2 = h.parse_date(row.pop("date of birth 2") or None, DATE_FORMATS)
    birth_place = row.pop("place of birth")
    nationality = row.pop("nationality")
    passport_number = row.pop("passport no.")
    position = row.pop("position")
    po_box = row.pop("postal code")
    fiscal_code = row.pop("fiscal code")
    phone_number = row.pop("phone number")
    address_1 = row.pop("address_1")
    address_2 = row.pop("address_2")
    city = row.pop("city")
    country = row.pop("country")
    address = h.make_address(
        context,
        full=address_1,
        remarks=address_2,
        po_box=po_box,
        city=city,
        country=country,
    )
    entity_type = row.pop("type")

    if entity_type == "Person":
        entity = context.make("Person")
        entity.id = context.make_id(full_name, birth_date_1, birth_place)
        entity.add("name", full_name)
        entity.add("alias", other_name)  # separated by comma
        entity.add("birthDate", birth_date_1)
        if birth_date_2:
            entity.add("birthDate", birth_date_2)
        entity.add("birthPlace", birth_place)
        # Handle multiple nationalities
        entity.add("nationality", [n.strip() for n in nationality.split("/")])
        entity.add("passportNumber", passport_number)
        h.copy_address(entity, address)
        entity.add("address", address)
        entity.add("taxNumber", fiscal_code)
        entity.add("phone", phone_number)
        entity.add("position", position)
        entity.add("topics", "sanction")
        entity.add(
            "program",
            "Romania Government Decision No. 1.272/2005: List of Suspected Terrorists",
        )
        # Emit the entity
        context.emit(entity, target=True)
    elif entity_type == "Organization":
        entity = context.make("Organization")
        entity.id = context.make_id(full_name, po_box, address_1)
        entity.add("name", full_name)
        entity.add("alias", other_name)  # separated by semicolon
        h.copy_address(entity, address)
        entity.add("address", address)
        entity.add("topics", "sanction")
        entity.add(
            "program",
            "Romania Government Decision No. 1.272/2005: List of Suspected Terrorists",
        )
        # Emit the entity
        context.emit(entity, target=True)
    else:
        context.log.warning("Unhandled entity type", type=entity_type)


def crawl(context: Context):
    path = context.fetch_resource("source.csv", context.data_url)
    with open(path, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            crawl_row(context, row)
