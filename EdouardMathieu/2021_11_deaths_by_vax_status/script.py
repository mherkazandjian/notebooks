import datetime
import requests

import epiweeks
import pandas as pd


SOURCE_USA = "https://data.cdc.gov/api/views/3rge-nu2a/rows.csv?accessType=DOWNLOAD"
SOURCE_CHL = "https://raw.githubusercontent.com/MinCiencia/Datos-COVID19/master/output/producto89/incidencia_en_vacunados_edad.csv"
SOURCE_ENG = "input/referencetable2.xlsx"
SOURCE_CHE = "https://www.covid19.admin.ch/api/data/context"

VACCINE_MAPPING = {
    "Janssen": "Johnson&Johnson",
    "Pfizer": "Pfizer/BioNTech",
    "all_types": "Fully vaccinated (all vaccines)",
}


def epiweek_to_date(row):
    return epiweeks.Week(row.Year, row.Week).enddate()


def process_usa(source: str):
    df = pd.read_csv(source)
    df = df[df.outcome == "death"]
    df["Vaccine product"] = df["Vaccine product"].replace(VACCINE_MAPPING)

    df.loc[df["Age adjusted vax IR"].notnull(), "crude_vax_ir"] = df[
        "Age adjusted vax IR"
    ]
    df.loc[df["Age adjusted unvax IR"].notnull(), "crude_unvax_ir"] = df[
        "Age adjusted unvax IR"
    ]

    vax = df[["Age group", "MMWR week", "Vaccine product", "Crude vax IR"]].rename(
        columns={
            "Crude vax IR": "incidence_rate",
            "Vaccine product": "vaccine_product",
        }
    )

    unvax = (
        df[["Age group", "MMWR week", "Vaccine product", "Crude unvax IR"]]
        .rename(
            columns={
                "Crude unvax IR": "incidence_rate",
                "Vaccine product": "vaccine_product",
            }
        )
        .assign(vaccine_product="Unvaccinated")
        .drop_duplicates()
    )

    df = pd.concat([vax, unvax], ignore_index=True).rename(
        columns={
            "Age group": "age_group",
            "MMWR week": "epiweek",
        }
    )

    df = (
        df.pivot(
            index=["age_group", "epiweek"],
            columns="vaccine_product",
            values="incidence_rate",
        )
        .reset_index()
        .rename(columns={"epiweek": "Week", "age_group": "Entity"})
    )

    assert (
        df.Week.min() == 14
    ), "New data for 2022 has been added! Revise epiweek_to_date for USA"
    df = df.assign(Year=2021)
    df["Year"] = df.apply(epiweek_to_date, axis=1)
    df["Year"] = (pd.to_datetime(df.Year) - pd.to_datetime("20210101")).dt.days
    df = df.drop(columns="Week")[
        [
            "Entity",
            "Year",
            "Fully vaccinated (all vaccines)",
            "Johnson&Johnson",
            "Moderna",
            "Pfizer/BioNTech",
            "Unvaccinated",
        ]
    ]

    df["Entity"] = df.Entity.replace({"all_ages_adj": "All ages"})

    df.to_csv(
        "output/COVID-19 - Deaths by vaccination status - United States.csv",
        index=False,
    )


def process_chl(source: str):
    df = pd.read_csv(
        source,
        usecols=[
            "semana_epidemiologica",
            "grupo_edad",
            "estado_vacunacion",
            "incidencia_def",
        ],
    ).rename(
        columns={
            "semana_epidemiologica": "Week",
            "grupo_edad": "Entity",
            "estado_vacunacion": "status",
            "incidencia_def": "rate",
        }
    )

    assert set(df.status) == {
        "sin esquema completo",
        "con esquema completo",
        "con dosis refuerzo > 14 dias",
    }

    df = df[df.Entity != "Total"]
    df["Entity"] = df.Entity.replace(
        {
            "06 - 11 años": "06-11",
            "12 - 20 años": "12-20",
            "21 - 30 años": "21-30",
            "31 - 40 años": "31-40",
            "41 - 50 años": "41-50",
            "51 - 60 años": "51-60",
            "61 - 70 años": "61-70",
            "71 - 80 años": "71-80",
            "81 años o más": "81+",
        }
    )

    # Age standardization based on single-year population estimates by the United Nations
    age_pyramid = {
        "06-11": 1521945,
        "12-20": 2244380,
        "21-30": 2980833,
        "31-40": 2932270,
        "41-50": 2574347,
        "51-60": 2345262,
        "61-70": 1774551,
        "71-80": 964821,
        "81+": 494118,
    }
    df["age_group_standard"] = df.Entity.replace(age_pyramid)
    df["age_group_proportion"] = df.age_group_standard / sum(age_pyramid.values())
    df["age_specific_adjusted_rate"] = df.rate * df.age_group_proportion
    all_ages = (
        df[["Week", "status", "age_specific_adjusted_rate"]]
        .groupby(["Week", "status"], as_index=False)
        .sum()
        .rename(columns={"age_specific_adjusted_rate": "rate"})
        .assign(Entity="All ages")
    )
    df = df.drop(
        columns=[
            "age_group_standard",
            "age_group_proportion",
            "age_specific_adjusted_rate",
        ]
    )
    df = pd.concat([df, all_ages], ignore_index=True)

    status_mapping = {
        "sin esquema completo": "Unvaccinated or not fully vaccinated",
        "con esquema completo": "Fully vaccinated",
        "con dosis refuerzo > 14 dias": "Fully vaccinated + booster",
    }
    assert set(status_mapping.keys()) == set(df.status)
    df["status"] = df.status.replace(status_mapping)

    df.loc[df.Week >= 31, "Year"] = 2021
    df.loc[df.Week <= 20, "Year"] = 2022
    assert df.Week.notnull().all(), "Revise epiweek_to_date for Chile"
    df["Year"] = df.Year.astype(int)
    if df.Week.min() == 0:
        df["Week"] = df.Week + 1
    df["Year"] = df.apply(epiweek_to_date, axis=1)
    df["Year"] = (pd.to_datetime(df.Year) - pd.to_datetime("20210101")).dt.days
    df = df[df.Year < df.Year.max()].drop(columns="Week")

    df = df.pivot(
        index=["Entity", "Year"], columns="status", values="rate"
    ).reset_index()

    df.to_csv(
        "output/COVID-19 - Deaths by vaccination status - Chile.csv",
        index=False,
    )


def process_eng(source: str):
    # All ages
    df = pd.read_excel(source, sheet_name="Table 1", skiprows=4)
    df = df[df.index < df.index[df["Month"].isna()].min()].rename(
        columns={"Month": "Year"}
    )

    unvax = df[
        [
            "Year",
            "Age-standardised mortality rate per 100,000 person-years",
            "Unnamed: 4",
        ]
    ].assign(Entity="All ages")
    unvax = (
        unvax[unvax["Unnamed: 4"] != "u"]
        .drop(columns="Unnamed: 4")
        .rename(
            columns={
                "Age-standardised mortality rate per 100,000 person-years": "Unvaccinated"
            }
        )
    )

    vax = df[
        [
            "Year",
            "Age-standardised mortality rate per 100,000 person-years.4",
            "Unnamed: 32",
        ]
    ].assign(Entity="All ages")
    vax = (
        vax[vax["Unnamed: 32"] != "u"]
        .drop(columns="Unnamed: 32")
        .rename(
            columns={
                "Age-standardised mortality rate per 100,000 person-years.4": "Fully vaccinated"
            }
        )
    )

    df = pd.merge(vax, unvax, how="outer", on=["Year", "Entity"])

    # Age groups
    by_age = pd.read_excel(source, sheet_name="Table 5", skiprows=3, na_values=":")
    by_age = by_age[by_age.index < by_age.index[by_age["Month"].isna()].min()].rename(
        columns={"Month": "Year"}
    )
    by_age = by_age[
        by_age["Vaccination status"].isin(
            ["Unvaccinated", "21 days or more after second dose"]
        )
    ].replace({"21 days or more after second dose": "Fully vaccinated"})
    by_age = (
        by_age[by_age["Unnamed: 6"] != "u"][
            [
                "Year",
                "Vaccination status",
                "Age-group",
                "Age-standardised mortality rate per 100,000 person-years",
            ]
        ]
        .pivot(
            index=["Year", "Age-group"],
            columns="Vaccination status",
            values="Age-standardised mortality rate per 100,000 person-years",
        )
        .reset_index()
        .rename(columns={"Age-group": "Entity"})
    )
    by_age = by_age[by_age.Entity != "10-59"]

    # Concatenate
    df = pd.concat([df, by_age], ignore_index=True)[
        ["Entity", "Year", "Unvaccinated", "Fully vaccinated"]
    ]

    # The data is reported in 100,000 person-years. To get monthly death rates, we divide by 12
    df[["Unvaccinated", "Fully vaccinated"]] = (
        df[["Unvaccinated", "Fully vaccinated"]].div(12).round(1)
    )

    assert (
        len(df.Year.unique()) < 12
    ), "New data for 2022 has been added! Revise epiweek_to_date for England"
    df["Year"] = pd.to_datetime("15 " + df.Year + " 2021")
    df["Year"] = (df.Year - pd.to_datetime("20210101")).dt.days

    df.to_csv(
        "output/COVID-19 - Deaths by vaccination status - England.csv",
        index=False,
    )


def process_che(source: str):
    response = requests.get(source).json()
    context = response["sources"]["individual"]["csv"]
    data_url = context["weekly"]["byAge"]["deathVaccPersons"]
    df = pd.read_csv(data_url)

    assert set(df.vaccination_status) == {
        "not_vaccinated",
        "fully_vaccinated_no_booster",
        "fully_vaccinated_first_booster",
        "partially_vaccinated",
        "fully_vaccinated",
        "unknown",
    }, f"New vaccination_status: {set(df.vaccination_status)}"

    df = df[
        (df.vaccine == "all")
        & (
            df.vaccination_status.isin(
                [
                    "not_vaccinated",
                    "fully_vaccinated_no_booster",
                    "fully_vaccinated_first_booster",
                ]
            )
        )
        & (df.geoRegion == "CHFL")
        & (df.type == "COVID19Death")
        & (df.timeframe_all == True)
        & (df["pop"].notnull())
        & (df["pop"] >= 100)
        & (df.date < df.date.max())
    ][["date", "altersklasse_covid19", "vaccination_status", "entries", "pop"]].rename(
        columns={
            "date": "Year",
            "altersklasse_covid19": "Entity",
        }
    )

    df[["Year", "Week"]] = (
        df.Year.astype(str).str.extract(r"(\d{4})(\d{2})").astype(int)
    )
    df["Year"] = df.apply(epiweek_to_date, axis=1)
    df["Year"] = (pd.to_datetime(df.Year) - pd.to_datetime("20210101")).dt.days
    df = df.drop(columns="Week")

    df = df[-df.Entity.isin(["all", "Unbekannt"])]
    df["Entity"] = df.Entity.replace(
        {
            "0 - 9": "00-09",
            "10 - 19": "10-19",
            "20 - 29": "20-29",
            "30 - 39": "30-39",
            "40 - 49": "40-49",
            "50 - 59": "50-59",
            "60 - 69": "60-69",
            "70 - 79": "70-79",
            "80+": "80+",
        }
    )

    df["rate"] = 100000 * df.entries / df["pop"]

    # Age standardization based on single-year population estimates by the United Nations
    age_pyramid = {
        "00-09": 892899,
        "10-19": 841842,
        "20-29": 1027126,
        "30-39": 1220774,
        "40-49": 1163760,
        "50-59": 1323318,
        "60-69": 1006299,
        "70-79": 765354,
        "80+": 474122,
    }
    df["age_group_standard"] = df.Entity.replace(age_pyramid)
    df["age_group_proportion"] = df.age_group_standard / sum(age_pyramid.values())
    df["age_specific_adjusted_rate"] = df.rate * df.age_group_proportion
    all_ages = (
        df[["Year", "vaccination_status", "age_specific_adjusted_rate"]]
        .groupby(["Year", "vaccination_status"], as_index=False)
        .sum()
        .rename(columns={"age_specific_adjusted_rate": "rate"})
        .assign(Entity="All ages")
    )
    df = df.drop(
        columns=[
            "age_group_standard",
            "age_group_proportion",
            "age_specific_adjusted_rate",
        ]
    )
    df = pd.concat([df, all_ages], ignore_index=True)

    df = (
        df.drop(columns=["entries", "pop"])
        .pivot(index=["Entity", "Year"], columns="vaccination_status", values="rate")
        .reset_index()
        .rename(
            columns={
                "fully_vaccinated_no_booster": "Fully vaccinated, no booster",
                "not_vaccinated": "Unvaccinated",
                "fully_vaccinated_first_booster": "Fully vaccinated + booster",
            }
        )
    )

    df.to_csv(
        "output/COVID-19 - Deaths by vaccination status - Switzerland and Liechtenstein.csv",
        index=False,
    )


def main():
    process_che(SOURCE_CHE)
    process_usa(SOURCE_USA)
    process_chl(SOURCE_CHL)
    process_eng(SOURCE_ENG)


if __name__ == "__main__":
    main()
