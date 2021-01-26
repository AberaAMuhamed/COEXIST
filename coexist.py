#!/usr/bin/env python

"""
## Source: https://github.com/gbohner/coexist/

## MIT License

Copyright (c) 2020 Gergo Bohner

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

"""

# Basic packages
import numpy as np
from scipy import integrate, stats, spatial
from scipy.special import expit, binom
import pandas as pd
import copy
import warnings
import argparse


# Building parameter/computation graph
import inspect
from collections import OrderedDict

# OS/filesystem tools
import time
from datetime import datetime, timedelta
import random
import string
import os
import shutil
import sys
import itertools
import json

################### COMMAND LINE RUN
# $ python3 coexist.py -days=200 -out=stateResults.csv

################### COMMAND LINE ARGS

parser = argparse.ArgumentParser(description="Get number of days to run simulation")
parser.add_argument("-days", dest="total_days", type=int, help="Number of days to run simulation")
parser.add_argument("-out", dest="outfile", type=str, help="Name of output file")

args = parser.parse_args()

total_days = args.total_days
outfile = args.outfile

# Set Working/Data dirs
workdir = os.getcwd()
data_folder = "inputs"
data_dir = f"{workdir}/{data_folder}"

############# Static Input Parameters#############  ref: "baked_in_parameters.ipynb"
with open(f"{data_dir}/sme_input.json") as jf:
    sme_input = json.load(jf)
print(data_dir)
# Population by Age (0-9, 10-19, ... 70-79, 80+) ref: https://en.wikipedia.org/wiki/Demographics_of_Ethiopia "AGE STRUCTURE"
agePopulationTotal = np.array(sme_input["agePopulationTotal"])
agePopulationRatio = agePopulationTotal / np.sum(agePopulationTotal)

## Social Mixing Matrices
# BASELINE
ageSocialMixingBaseline = (
    pd.read_csv(f"{data_dir}/social_mixing_BASELINE.csv", sep=",")
    .iloc[:, 1:]
    .values
)
ageSocialMixingBaseline = (ageSocialMixingBaseline + ageSocialMixingBaseline.T) / 2.0

# SOCIAL DISTANCING
ageSocialMixingDistancing = (
    pd.read_csv(f"{data_dir}/social_mixing_DISTANCE.csv", sep=",")
    .iloc[:, 1:]
    .values
)
ageSocialMixingDistancing = (
    ageSocialMixingDistancing + ageSocialMixingDistancing.T
) / 2.0

# Travel Data Gamma Distribution
travelMaxTime = 200
travelBaseRate = 5e-4  # How many people normally travel back to the country per day
travelDecline_mean = 15.0
travelDecline_slope = 1.0
travelInfection_peak = 1e-1
travelInfection_maxloc = 10.0
travelInfection_shape = 2.0

# Baseline States as defined in Uk model
# The state tensor State Dimensions: Health states (S, E and D are fixed to 1 dimension)
nI_symp = 2  # number of sympyomatic infected states
nI = (2 + nI_symp)  # number of total infected states (disease stages), the +2 are Exposed and I_nonsymptomatic
nR = 2  # number of recovery states (antibody development post-disease, IgM and IgG are two stages)
nHS = (2 + nI + nR)  # number of total health states, the +2: S, D are suspectible and dead
nAge = 9  # Age groups (risk groups) In accordance w Imperial #13 report (0-9, 10-19, ... 70-79, 80+)
nIso = 4  # Isolation states: None/distancing, Case isolation, Hospitalised, Hospital staff
nTest = 4  # Testing states: untested/negative, Virus positive, Antibody positive, Both positive

stateTensor = np.zeros((nAge, nHS, nIso, nTest))

### Hospitalization

# Hospitalization rate by age: mapped UK to ETH population (see "baked_in_parameters")
yearly_baseline_admissions = np.array(sme_input["yearly_baseline_admissions"])
ageHospitalisationRateBaseline = yearly_baseline_admissions / (365 * agePopulationTotal)


# Average days in the hospital by age
ageHospitalMeanLengthOfStay = np.array(sme_input["ageHospitalMeanLengthOfStay"])
ageHospitalisationRecoveryRateBaseline = 1.0 / ageHospitalMeanLengthOfStay

# Ratio of Hospital Staff by Age
ageNhsClinicalStaffPopulationRatio = np.array(sme_input["ageNhsClinicalStaffPopulationRatio"])

# Rate of transmission given contact for differnt states [exposed, asymptomatic, I1 (symptomatic early), I2 (symptomatic late)]
transmissionInfectionStage = np.array(sme_input["transmissionInfectionStage"])


############# USER INPUT PARAMETERS ############# from "USER_build_data.ipynb"
with open(f"{data_dir}/user_input.json") as jf:
    user_input = json.load(jf)

# Number of Days in Isolation
nDaysInHomeIsolation = user_input["nDaysInHomeIsolation"]

tStartSocialDistancing = pd.to_datetime(
    user_input["tStartSocialDistancing"], format="%Y-%m-%d"
)
tStopSocialDistancing = pd.to_datetime(
    user_input["tStopSocialDistancing"], format="%Y-%m-%d"
)
tStartImmunityPassports = pd.to_datetime(
    user_input["tStartImmunityPassports"], format="%Y-%m-%d"
)
tStopImmunityPassports = pd.to_datetime(
    user_input["tStopImmunityPassports"], format="%Y-%m-%d"
)
tStartQuarantineCaseIsolation = pd.to_datetime(
    user_input["tStartQuarantineCaseIsolation"], format="%Y-%m-%d"
)
tStopQuarantineCaseIsolation = pd.to_datetime(
    user_input["tStopQuarantineCaseIsolation"], format="%Y-%m-%d"
)
CONST_DATA_START_DATE = user_input["CONST_DATA_START_DATE"]
CONST_DATA_CUTOFF_DATE = user_input["CONST_DATA_CUTOFF_DATE"]


# Risk of Admission by age
totalCOVIDAdmitted_byAge_regroup = user_input["percent_admitted"] * agePopulationTotal
relativeAdmissionRisk_given_COVID_by_age = (totalCOVIDAdmitted_byAge_regroup / agePopulationTotal)

relativeAdmissionRisk_given_COVID_by_age /= np.mean(relativeAdmissionRisk_given_COVID_by_age)
relativeAdmissionRisk_given_COVID_by_age -= 1

# Risk of Death by Age
totalDeaths_byAge_regroupLinear = user_input["deaths_by_age"]
relativeDeathRisk_given_COVID_by_age = (totalDeaths_byAge_regroupLinear / agePopulationTotal)
relativeDeathRisk_given_COVID_by_age /= np.mean(relativeDeathRisk_given_COVID_by_age)
relativeDeathRisk_given_COVID_by_age -= 1


# Death Rate by Age
caseFatalityRatioHospital_given_COVID_by_age = (totalDeaths_byAge_regroupLinear / totalCOVIDAdmitted_byAge_regroup)

# ageRelativeRecoverySpeed = np.array([0.2]*5+[-0.1, -0.2, -0.3, -0.5]) # TODO - this is a guess, find data and fix
ageRelativeRecoverySpeed = np.array([0.0] * 9)  # For now we make it same for everyone, makes calculations easier

# Social Mixing WHILE Isolating (rule-breakers)
percent_not_isolating = np.array(user_input["percent_not_isolating"])
percent_isolating_mat = np.array([percent_not_isolating,] * nAge).transpose()

# ageSocialMixingIsolation = percent_isolating_mat*ageSocialMixingDistancing
ageSocialMixingIsolation = np.zeros_like(ageSocialMixingBaseline)  # OR PERFECT ISOLATION

# From coexist model
# Getting Infected in the Hospital
elevatedMixingRatioInHospital = 3.0
withinHospitalSocialMixing = elevatedMixingRatioInHospital * np.sum(np.dot(agePopulationRatio, ageSocialMixingBaseline))

# Calculate initial hospitalisation (occupancy), that will be used to initialise the model
initBaselineHospitalOccupancyEquilibriumAgeRatio = ageHospitalisationRateBaseline / (ageHospitalisationRateBaseline + ageHospitalisationRecoveryRateBaseline)

# Extra rate of hospitalisation due to COVID-19 infection stages; Symptom to hospitalisation is 5.76 days on average (Imperial #8)
infToHospitalExtra = np.array(sme_input["infToHospitalExtra"])

# We do know at least how age affects these risks:
# For calculations see data_cleaning_py.ipynb, calculations from CHESS dataset as per 05 Apr
riskOfAEAttandance_by_age = np.array(sme_input["riskOfAEAttandance_by_age"])
# riskOfAEAttandance_by_age = np.array([0.41261361, 0.31560648, 0.3843979 , 0.30475704, 0.26659415,0.25203475, 0.24970244, 0.31549102, 0.65181376])


testingStartDate = pd.to_datetime(user_input["testingStartDate"], format="%Y-%m-%d")
ageTestingData = sme_input["ageTestingData"]
df_CHESS_numTests_regroup = pd.DataFrame({testingStartDate: ageTestingData}).T

#df_CHESS_numTests_regroup.index = df_CHESS_numTests.index

# ## Initialise the model

# Initialise state
stateTensor_init = copy.deepcopy(stateTensor)
# Populate
stateTensor_init[:, 0, 0, 0] = agePopulationTotal
# Move hospital staff to working in hospital
stateTensor_init[:, 0, 0, 0] -= ageNhsClinicalStaffPopulationRatio * agePopulationTotal
stateTensor_init[:, 0, 3, 0] += ageNhsClinicalStaffPopulationRatio * agePopulationTotal
# Move people to hospital according to baseline occupation (move only from normal people, not hospital staff!)
stateTensor_init[:, 0, 2, 0] += (
    initBaselineHospitalOccupancyEquilibriumAgeRatio * stateTensor_init[:, 0, 0, 0]
)
stateTensor_init[:, 0, 0, 0] -= (
    initBaselineHospitalOccupancyEquilibriumAgeRatio * stateTensor_init[:, 0, 0, 0]
)


def regroup_by_age(
    inp,  # first dimension is ages, others don't matter.
    fromAgeSplits,
    toAgeSplits,
    maxAge=100.0,
    maxAgeWeight=5.0,
):
    fromAgeSplits = np.concatenate(
        [np.array([0]), fromAgeSplits, np.array([maxAge])]
    )  # Add a zero at beginning for calculations
    toAgeSplits = np.concatenate(
        [np.array([0]), toAgeSplits, np.array([maxAge])]
    )  # Add inf at end for calculations

    def getOverlap(a, b):
        return max(0, min(a[1], b[1]) - max(a[0], b[0]))

    out = np.zeros((len(toAgeSplits) - 1,) + inp.shape[1:])
    for from_ind in range(1, len(fromAgeSplits)):
        # Redistribute to the new bins by calculating how many years in from_ind-1:from_ind falls into each output bin
        cur_out_distribution = [
            getOverlap(
                toAgeSplits[cur_to_ind - 1 : cur_to_ind + 1],
                fromAgeSplits[from_ind - 1 : from_ind + 1],
            )
            for cur_to_ind in range(1, len(toAgeSplits))
        ]

        if cur_out_distribution[-1] > 0:
            cur_out_distribution[
                -1
            ] = maxAgeWeight  # Define the relative number of ages if we have to distribute between second to last and last age groups

        cur_out_distribution = cur_out_distribution / np.sum(cur_out_distribution)

        for to_ind in range(len(out)):
            out[to_ind] += cur_out_distribution[to_ind] * inp[from_ind - 1]

    return out


# Build the nested parameter/computation graph of a single function.
def build_paramDict(cur_func):
    """
    This function iterates through all inputs of a function,
    and saves the default argument names and values into a dictionary.

    If any of the default arguments are functions themselves, then recursively (depth-first) adds an extra field to
    the dictionary, named <funcName + "_params">, that contains its inputs and arguments.

    The output of this function can then be passed as a "kwargs" object to the highest level function,
    which will then pass the parameter values to the lower dictionary levels appropriately
    """

    paramDict = OrderedDict()

    allArgs = inspect.getfullargspec(cur_func)

    # Check if there are any default parameters, if no, just return empty dict
    if allArgs.defaults is None:
        return paramDict

    for argname, argval in zip(
        allArgs.args[-len(allArgs.defaults) :], allArgs.defaults
    ):
        # Save the default argument
        paramDict[argname] = argval
        # If the default argument is a function, inspect it for further

        if callable(argval):
            # print(argname)
            paramDict[argname + "_params"] = build_paramDict(argval)

    return paramDict


# Do a mapping between dictionary and parameter table row and vice versa (for convenient use)

# Flatten the dictionary into a table with a single row (but many column):
def paramDict_toTable(paramDict):
    paramTable = pd.DataFrame()

    def paramDictRecurseIter(cur_table, cur_dict, preString):
        # Iterate through the dictionary to find all keys not ending in "_params",
        # and add them to the table with name <preString + key>
        #
        # If the key doesn end in "_params", then append the key to preString, in call this function on the value (that is a dict)
        for key, value in cur_dict.items():
            if key.endswith("_params"):
                paramDictRecurseIter(cur_table, value, preString + key + "_")
            else:
                paramTable[preString + key] = [value]

        # For the rare case where we want to keep an empty dictionary, the above for cycle doesn't keep it
        if len(cur_dict) == 0:
            paramTable[preString] = [OrderedDict()]

        return cur_table

    return paramDictRecurseIter(paramTable, paramDict, preString="")


def paramTable_toDict(paramTable, defaultDict=None):
    # enable to pass a default dict (if paramTable is incomplete), in which we'll just add / overwrite the values
    paramDict = defaultDict if defaultDict is not None else OrderedDict()

    def placeArgInDictRecurse(argName, argVal, cur_dict):
        # Find all "_params_" in the argName, and for each step more and more down in the dictionary
        strloc = argName.find("_params_")
        if strloc == -1:
            # We're at the correct level of dictionary
            cur_dict[argName] = argVal
            return cur_dict
        else:
            # step to the next level of dictionary
            nextKey = argName[: strloc + len("_params_") - 1]
            nextArgName = argName[strloc + len("_params_") :]
            if not nextKey in cur_dict:
                cur_dict[nextKey] = OrderedDict()
            placeArgInDictRecurse(nextArgName, argVal, cur_dict[nextKey])

        return cur_dict

    for key in paramTable.columns:
        paramDict = placeArgInDictRecurse(key, paramTable.at[0, key], paramDict)

    return paramDict


# Helper function to adjust average rates to age-aware rates
def adjustRatesByAge_KeepAverageRate(
    rate, ageRelativeAdjustment, agePopulationRatio=agePopulationRatio, maxOutRate=1e20
):
    """This is a helper function and wont be picked up as a model parameter!"""
    if rate == 0:
        return np.zeros_like(ageRelativeAdjustment)
    if rate >= maxOutRate:
        warnings.warn(
            "covidTesting::adjustRatesByAge_KeepAverageRate Input rate {} > maxOutRate {}, returning input rates".format(
                rate, maxOutRate
            )
        )
        return rate * np.ones_like(ageRelativeAdjustment)
    out = np.zeros_like(ageRelativeAdjustment)
    out[0] = maxOutRate + 1  # just to start the while loop below

    while np.sum(out >= maxOutRate) > 0:
        corrFactor = np.sum(agePopulationRatio / (1 + ageRelativeAdjustment))
        out = rate * (1 + ageRelativeAdjustment) * corrFactor
        if np.sum(out >= maxOutRate) > 0:
            warnings.warn(
                f"covidTesting::adjustRatesByAge_KeepAverageRate Adjusted rate larger than {maxOutRate} encountered, reducing ageAdjustment variance by 10%".format(
                    maxOutRate
                )
            )
            tmp_mean = np.mean(ageRelativeAdjustment)
            ageRelativeAdjustment = tmp_mean + np.sqrt(0.9) * (
                ageRelativeAdjustment - tmp_mean
            )
    return out


# Also add new infected from travelling based on time-within-simulation

# TODO - get real travel data to make these numbers more realistic. For now based on the following assumptions:
# - people's age distribution in travel is square of the usual age distribution
# - travel rates declined from a base rate as a sigmoid due to border closures, with given mean and slope
# - infection rates due to travel are modelled as a gamma pdf over time, with given peak value, loc, and scale parameter
def trFunc_travelInfectionRate_ageAdjusted(
    t,  # Time (int, in days) within simulation
    travelMaxTime=travelMaxTime,
    travelBaseRate=travelBaseRate,  # How many people normally travel back to the country per day # TODO - get data
    travelDecline_mean=travelDecline_mean,
    travelDecline_slope=travelDecline_slope,
    travelInfection_peak=travelInfection_peak,
    travelInfection_maxloc=travelInfection_maxloc,
    travelInfection_shape=travelInfection_shape,
    **kwargs,
):

    tmpTime = np.arange(travelMaxTime)
    # nAge x T TODO get some realistic data on this
    travelAgeRateByTime = travelBaseRate * np.outer(
        agePopulationRatio,
        1 - expit((tmpTime - travelDecline_mean) / travelDecline_slope),
    )

    # 1 x T TODO get some realistic data on this, maybe make it age weighted
    travelContractionRateByTime = stats.gamma.pdf(
        tmpTime,
        a=travelInfection_shape,
        loc=0.0,
        scale=travelInfection_maxloc / (travelInfection_shape - 1),
    )
    travelContractionRateByTime = (
        travelInfection_peak
        * travelContractionRateByTime
        / np.max(travelContractionRateByTime)
    )

    if t >= travelAgeRateByTime.shape[-1]:
        return np.zeros(travelAgeRateByTime.shape[0])
    else:
        return travelAgeRateByTime[:, int(t)] * travelContractionRateByTime[int(t)]


# Overall new infections include within quarantine and hospital infections
# ------------------------------------------------------------------------


def trFunc_newInfections_Complete(
    stateTensor,
    policySocialDistancing,  # True / False, no default because it's important to know which one we use at any moment!
    policyImmunityPassports,  # True / False, no default because it's important to know which one we use at any moment!
    ageSocialMixingBaseline=ageSocialMixingBaseline,
    ageSocialMixingDistancing=ageSocialMixingDistancing,
    ageSocialMixingIsolation=ageSocialMixingIsolation,
    withinHospitalSocialMixing=withinHospitalSocialMixing,
    transmissionInfectionStage=transmissionInfectionStage,
    **kwargs,
):
    """
    All new infections, given infected people on all different isolation states (normal, home, hospital)
    We use the following assumptions:

    - Infectiousness only depends on infection stage, not age or location

    - Hospitalised people are assumed to only mix with other hospitalised people (this includes staff!),
    in a non-age-dependent manner: withinHospitalSocialMixing

    If policySocialDistancing is True
    - Non- and home-isolated people mix with non- and home isolated via ageSocialMixingDistancing (averaging interactions)

    If policySocialDistancing is False, we assume home-isolation is taken more seriously, but with little effect on non-isolated people
    - Non-isolated people mix with each other via ageSocialMixingBaseline, and with home-isolated people via ageSocialMixingIsolation
    - Home-isolated people do not mix with each other

    This separation will help disentangle the effects of simply a blanket lessening of social distancing
    (keeping the policy True but with less effective ageSocialMixingDistancing matrix),
    vs case isolation (policy = False, but with serious ageSocialMixingIsolation)
    """

    ageIsoContractionRate = np.zeros((nAge, nIso, nTest))

    # Add non-hospital infections
    # --------------------------------

    curNonIsolatedSocialMixing = (
        ageSocialMixingDistancing if policySocialDistancing else ageSocialMixingBaseline
    )

    # Add baseline interactions only between non-isolated people
    for k1 in [0, 3]:
        for k2 in [0, 3]:
            ageIsoContractionRate[:, k1, :] += np.expand_dims(
                np.matmul(
                    curNonIsolatedSocialMixing,
                    np.einsum(
                        "ijl,j->i",
                        stateTensor[:, 1 : (nI + 1), k2, :],
                        transmissionInfectionStage,
                    ),  # all infected in non-isolation
                ),
                axis=1,
            )

    if policyImmunityPassports:
        # If the immunity passports policy is on, everyone who tested antibody positive, can roam freely
        # Therefore replace the interactions between people with testingState = 2 with ageSocialMixingBaseline
        # we do this by using the distributive property of matrix multiplication, and adding extra interactions
        # "ageSocialMixingBaseline"-"curNonIsolatedSocialMixing" with each other (this is zero if no social distancing!)
        # TODO - this is a bit hacky?, but probably correct - double check though!
        for k1 in [0, 3]:
            for k2 in [0, 3]:
                ageIsoContractionRate[:, k1, 2:] += np.matmul(
                    ageSocialMixingBaseline - curNonIsolatedSocialMixing,
                    np.einsum(
                        "ijk,j->ik",
                        stateTensor[:, 1 : (nI + 1), k2, 2:],
                        transmissionInfectionStage,
                    ),  # all infected in non-isolation
                )

    # Add isolation interactions only between isolated and non-isolated people
    # non-isolated contracting it from isolated
    for k1 in [0, 3]:
        ageIsoContractionRate[:, k1, :] += np.expand_dims(
            np.matmul(
                ageSocialMixingIsolation,
                np.einsum(
                    "ijl,j->i",
                    stateTensor[:, 1 : (nI + 1), 1, :],
                    transmissionInfectionStage,
                ),  # all infected in isolation
            ),
            axis=1,
        )

    # isolated contracting it from non-isolated
    for k1 in [0, 3]:
        ageIsoContractionRate[:, 1, :] += np.expand_dims(
            np.matmul(
                ageSocialMixingIsolation,
                np.einsum(
                    "ijl,j->i",
                    stateTensor[:, 1 : (nI + 1), k1, :],
                    transmissionInfectionStage,
                ),  # all infected in non-hospital, non-isolation
            ),
            axis=1,
        )

        # isolated cannot contracting it from another isolated

    # Add in-hospital infections (of hospitalised patients, and staff)
    # --------------------------------
    # (TODO - within hospitals we probably want to take into effect the testing state;
    #      tested people are better isolated and there's less mixing)

    ageIsoContractionRate[:, 2:, :] += np.expand_dims(
        withinHospitalSocialMixing
        * np.einsum(
            "ijkl,j->i", stateTensor[:, 1 : (nI + 1), 2:, :], transmissionInfectionStage
        ),  # all infected in hospital (sick or working)
        axis=(1, 2),
    )

    return ageIsoContractionRate / np.sum(
        stateTensor
    )  # Normalise the rate by total population


# Build the transition tensor from any non-hospitalised state to a hospitalised state
# (being in home quarantine is assumed to affect only the infection probability [below], not the hospitalisation probability)
# caseIsolationHospitalisationRateAdjustment = 1.

# This function takes as input the number of people in given age and health state, and in any non-hospitalised state
# and returns the number of people staying in the same age and health state,
# but now hospitalised (the rest of people remain in whatever state they were in)


def trFunc_HospitalAdmission(
    ageHospitalisationRateBaseline=ageHospitalisationRateBaseline,
    infToHospitalExtra=infToHospitalExtra,
    ageRelativeExtraAdmissionRiskToCovid=relativeAdmissionRisk_given_COVID_by_age
    * riskOfAEAttandance_by_age,
    **kwargs,
):

    # This tensor will pointwise multiply an nAge x nHS slice of the stateTensor
    trTensor_HospitalAdmission = np.zeros((nAge, nHS))

    ageAdjusted_infToHospitalExtra = copy.deepcopy(
        np.repeat(infToHospitalExtra[np.newaxis], nAge, axis=0)
    )
    for ii in range(ageAdjusted_infToHospitalExtra.shape[1]):
        # Adjust death rate by age dependent disease severity
        ageAdjusted_infToHospitalExtra[:, ii] = adjustRatesByAge_KeepAverageRate(
            infToHospitalExtra[ii],
            ageRelativeAdjustment=ageRelativeExtraAdmissionRiskToCovid,
        )

    # Add baseline hospitalisation to all non-dead states
    trTensor_HospitalAdmission[:, :-1] += np.expand_dims(
        ageHospitalisationRateBaseline, -1
    )

    # Add COVID-caused hospitalisation to all infected states (TODO: This is summation of rates for independent processes, should be correct, but check)
    trTensor_HospitalAdmission[:, 1 : (nI + 1)] += ageAdjusted_infToHospitalExtra

    return trTensor_HospitalAdmission


# Recovery rates (hospital discharge)
# ------------------------------------

# Higher-than-normal discharge rate for people who recovered (as they were likely to be in hospital mostly due to the virus)
# TODO - check with health experts if this is correct assumption; probably also depends on testing state


def trFunc_HospitalDischarge(
    ageHospitalisationRecoveryRateBaseline=ageHospitalisationRecoveryRateBaseline,
    dischargeDueToCovidRateMultiplier=3.0,
    **kwargs,
):

    trTensor_HospitalDischarge = np.zeros((nAge, nHS))

    # Baseline discharges apply to all non-symptomatic patients (TODO: take into account testing state!)
    trTensor_HospitalDischarge[:, :3] += ageHospitalisationRecoveryRateBaseline[
        :, np.newaxis
    ]

    # No discharges for COVID symptomatic people from the hospital until they recover
    # TODO - check with health experts if this is correct assumption; probably also depends on testing state
    trTensor_HospitalDischarge[:, 3:5] = 0.0

    trTensor_HospitalDischarge[:, 5:7] = (
        dischargeDueToCovidRateMultiplier
        * ageHospitalisationRecoveryRateBaseline[:, np.newaxis]
    )

    return trTensor_HospitalDischarge


def trFunc_diseaseProgression(
    # Basic parameters to adhere to
    nonsymptomatic_ratio=0.86,
    # number of days between measurable events
    infect_to_symptoms=5.0,
    # symptom_to_death = 16.,
    symptom_to_recovery=10.0,  # 20.5, #unrealiticly long for old people
    symptom_to_hospitalisation=5.76,
    hospitalisation_to_recovery=14.51,
    IgG_formation=15.0,
    # Age related parameters
    # for now we'll assume that all hospitalised cases are known (overall 23% of hospitalised COVID patients die. 9% overall case fatality ratio)
    caseFatalityRatioHospital_given_COVID_by_age=caseFatalityRatioHospital_given_COVID_by_age,
    ageRelativeRecoverySpeed=ageRelativeRecoverySpeed,
    # Unknown rates to estimate
    nonsymp_to_recovery=15.0,
    inverse_IS1_IS2=4.0,
    **kwargs,
):
    # Now we have all the information to build the age-aware multistage SIR model transition matrix
    # The full transition tensor is a sparse map from the Age x HealthState x isolation state to HealthState,
    # and thus is a 4th order tensor itself, representing a linear mapping
    # from "number of people aged A in health state B and isolation state C to health state D.
    trTensor_diseaseProgression = np.zeros((nAge, nHS, nIso, nHS))

    # Use basic parameters to regularise inputs
    E_IS1 = 1.0 / infect_to_symptoms
    # Numbers nonsymptomatic is assumed to be 86% -> E->IN / E-IS1 = 0.86/0.14
    E_IN = 0.86 / 0.14 * E_IS1

    # Nonsymptomatic recovery
    IN_R1 = 1.0 / nonsymp_to_recovery

    IS1_IS2 = 1.0 / inverse_IS1_IS2

    IS2_R1 = 1.0 / (symptom_to_recovery - inverse_IS1_IS2)

    R1_R2 = 1.0 / IgG_formation

    # Disease progression matrix # TODO - calibrate (together with transmissionInfectionStage)
    # rows: from-state, cols: to-state (non-symmetric!)
    # - this represent excess deaths only, doesn't contain baseline deaths!

    # Calculate all non-serious cases that do not end up in hospitals.
    # Note that we only have reliable death data from hospitals (NHS England), so we do not model people dieing outside hospitals
    diseaseProgBaseline = np.array(
        [
            # to: E,   IN,   IS1,   IS2,    R1,   R2,   D
            [0, E_IN, E_IS1, 0, 0, 0, 0],  # from E
            [0, 0, 0, 0, IN_R1, 0, 0],  # from IN
            [0, 0, 0, IS1_IS2, 0, 0, 0],  # from IS1
            [0, 0, 0, 0, IS2_R1, 0, 0],  # from IS2
            [0, 0, 0, 0, 0, R1_R2, 0],  # from R1
            [0, 0, 0, 0, 0, 0, 0],  # from R2
            [0, 0, 0, 0, 0, 0, 0],  # from D
        ]
    )

    ageAdjusted_diseaseProgBaseline = copy.deepcopy(
        np.repeat(diseaseProgBaseline[np.newaxis], nAge, axis=0)
    )

    # Modify all death and R1 rates:
    for ii in range(ageAdjusted_diseaseProgBaseline.shape[1]):
        # Adjust death rate by age dependent disease severity
        ageAdjusted_diseaseProgBaseline[:, ii, -1] = adjustRatesByAge_KeepAverageRate(
            ageAdjusted_diseaseProgBaseline[0, ii, -1],
            ageRelativeAdjustment=relativeDeathRisk_given_COVID_by_age,
        )

        # Adjust recovery rate by age dependent recovery speed
        ageAdjusted_diseaseProgBaseline[:, ii, -3] = adjustRatesByAge_KeepAverageRate(
            ageAdjusted_diseaseProgBaseline[0, ii, -3],
            ageRelativeAdjustment=ageRelativeRecoverySpeed,
            agePopulationRatio=agePopulationRatio,
        )

    ageAdjusted_diseaseProgBaseline_Hospital = copy.deepcopy(
        ageAdjusted_diseaseProgBaseline
    )
    # Calculate hospitalisation based rates, for which we do have data. Hospitalisation can end up with deaths

    # Make sure that the ratio of recoveries in hospital honour the case fatality ratio appropriately
    # IS2 -> death
    ageAdjusted_diseaseProgBaseline_Hospital[:, 3, -1] = (
        # IS2 -> recovery
        ageAdjusted_diseaseProgBaseline_Hospital[:, 3, -3]
        * (
            # multiply by cfr / (1-cfr) to get correct rate towards death
            caseFatalityRatioHospital_given_COVID_by_age
            / (1 - caseFatalityRatioHospital_given_COVID_by_age)
        )
    )

    # TODO - time to death might be incorrect overall without an extra delay state, especially for young people

    # Non-hospitalised disease progression
    for i1 in [0, 1, 3]:
        trTensor_diseaseProgression[:, 1:, i1, 1:] = ageAdjusted_diseaseProgBaseline

    # hospitalised disease progression
    trTensor_diseaseProgression[:, 1:, 2, 1:] = ageAdjusted_diseaseProgBaseline_Hospital

    return trTensor_diseaseProgression


# Test parameters
# ---------------
# assumptions about practical (not theoretical, see discrapancy in PCR!) parameters of tests
# TODO - but particular data and references from lit (or estimates based on previous similar tests)

# TODO - MANUAL! - this function is VERY specific to current health state setup, and needs to be manually edited if number of health states change
def inpFunc_testSpecifications(
    PCR_FNR_I1_to_R2=np.array([0.9, 0.4, 0.15, 0.35, 0.5, 0.8]),
    PCR_FPR=0.01,
    antigen_FNR_I1_to_R2=np.array([0.95, 0.6, 0.35, 0.45, 0.6, 0.9]),
    antigen_FPR=0.1,
    antibody_FNR_I1_to_R2=np.array([0.99, 0.85, 0.8, 0.65, 0.3, 0.05]),
    antibody_FPR_S_to_I4=np.array([0.05, 0.04, 0.03, 0.02, 0.01]),
):

    testSpecifications = pd.DataFrame(
        columns=["Name"],  # , "Infection stage"],#, "Sensitivity", "Specificity"],
        data=(["PCR"] * nHS + ["Antigen"] * (nHS) + ["Antibody"] * (nHS)),
    )

    testSpecifications["OutputTestState"] = (
        [1] * nHS + [1] * nHS + [2] * nHS
    )  # what information state does a pos test transition you to.

    testSpecifications["TruePosHealthState"] = (
        [np.arange(1, nI + 1)] * nHS
        + [np.arange(1, nI + 1)] * nHS
        + [np.arange(nI + 1, nI + nR + 1)] * nHS
    )  # what information state does a pos test transition you to.

    # In some health states some people are true negatives and some are true positives! (No, makes litte sense to use, just account for it in FPR? Only matters for test makers...)
    # testSpecifications['AmbiguousPosHealthState'] = [np.arange(nI+1, nI+nR+1)]*nHS + [np.arange(nI+1, nI+nR+1)]*nHS + [np.arange(1, nI+1)]*nHS # what information state does a pos test transition you to.

    testSpecifications["InputHealthState"] = list(np.tile(range(nHS), 3))

    # These numbers below are "defaults" illustrating the concept, but are modified by the inputs!!!

    testSpecifications[
        "FalseNegativeRate"
    ] = [  # ratio of positive (infected / immune) people missed by the test
        # For each health stage:
        #  S -> I1 (asymp) -> I2 (mild symp) -> I3 (symp, sick) -> I4 (symp, less sick) -> R1 / R2 (IgM, IgG avail) -> D
        # PCR
        0.0,
        0.9,
        0.4,
        0.15,
        0.35,
        0.5,
        0.8,
        0.0,
        # Antigen
        0.0,
        0.95,
        0.6,
        0.35,
        0.45,
        0.6,
        0.9,
        0.0,
        # Antibody
        0.0,
        0.99,
        0.85,
        0.8,
        0.65,
        0.3,
        0.05,
        0.0,
    ]

    testSpecifications.loc[1:6, "FalseNegativeRate"] = PCR_FNR_I1_to_R2
    testSpecifications.loc[9:14, "FalseNegativeRate"] = antigen_FNR_I1_to_R2
    testSpecifications.loc[17:22, "FalseNegativeRate"] = antibody_FNR_I1_to_R2

    testSpecifications[
        "FalsePositiveRate"
    ] = [  # ratio of negative (non-infected or not immune) people deemed positive by the test
        # PCR
        0.01,
        0.0,
        0.0,
        0.0,
        0.0,
        0.01,
        0.01,
        0.0,
        # Antigen
        0.1,
        0.0,
        0.0,
        0.0,
        0.0,
        0.1,
        0.1,
        0.0,
        # Antibody
        0.05,
        0.04,
        0.03,
        0.02,
        0.01,
        0.0,
        0.0,
        0.0,
    ]

    testSpecifications.loc[0, "FalsePositiveRate"] = PCR_FPR
    testSpecifications.loc[5:6, "FalsePositiveRate"] = PCR_FPR
    testSpecifications.loc[8, "FalsePositiveRate"] = antigen_FPR
    testSpecifications.loc[13:14, "FalsePositiveRate"] = antigen_FPR
    testSpecifications.loc[16:20, "FalsePositiveRate"] = antibody_FPR_S_to_I4

    return testSpecifications


# For PCR - we will model this (for now, for fitting we'll plug in real data!), as the sum of two sigmoids:
#   - initial stage of PHE ramping up its limited capacity (parameterised by total capacity, inflection day and slope of ramp-up)
#   - second stage of non-PHE labs joining in and ramping up capacity (this hasn't happened yet, but expected soon! same parameterisation)

# For the antigen / antibody tests we define a single sigmoidal capacity curve (starting later than PCR, but with potentially much higher total capacity)
# We further define a ratio between the production of the two, due to them requiring the same capabilities.


def trFunc_testCapacity(
    realTime,  # time within simulation (day)
    # PCR capacity - initial
    testCapacity_pcr_phe_total=1e4,
    testCapacity_pcr_phe_inflexday=pd.to_datetime("2020-03-25", format="%Y-%m-%d"),
    testCapacity_pcr_phe_inflexslope=5.0,
    # PCR capacity - increased
    testCapacity_pcr_country_total=1e5,
    testCapacity_pcr_country_inflexday=pd.to_datetime("2020-04-25", format="%Y-%m-%d"),
    testCapacity_pcr_country_inflexslope=10,
    # Antibody / antigen capacity
    testCapacity_antibody_country_firstday=pd.to_datetime(
        "2020-04-25", format="%Y-%m-%d"
    ),
    testCapacity_antibody_country_total=5e6,
    testCapacity_antibody_country_inflexday=pd.to_datetime(
        "2020-05-20", format="%Y-%m-%d"
    ),
    testCapacity_antibody_country_inflexslope=20,
    testCapacity_antigenratio_country=0.7,
    **kwargs,
):

    # Returns a dictionary with test names and number available at day "t"

    outPCR = (
        # phe phase
        testCapacity_pcr_phe_total
        * expit(
            (realTime - testCapacity_pcr_phe_inflexday).days
            / testCapacity_pcr_phe_inflexslope
        )
        +
        # whole country phase
        testCapacity_pcr_country_total
        * expit(
            (realTime - testCapacity_pcr_country_inflexday).days
            / testCapacity_pcr_country_inflexslope
        )
    )

    if realTime < testCapacity_antibody_country_firstday:
        outAntiTotal = 0.0
    else:
        outAntiTotal = testCapacity_antibody_country_total * expit(
            (realTime - testCapacity_antibody_country_inflexday).days
            / testCapacity_antibody_country_inflexslope
        )

    return {
        "PCR": outPCR,
        "Antigen": outAntiTotal * testCapacity_antigenratio_country,
        "Antibody": outAntiTotal * (1 - testCapacity_antigenratio_country),
    }


def inpFunc_testingDataCHESS_PCR(
    realTime, realTestData=df_CHESS_numTests_regroup, **kwargs
):
    def nearest(items, pivot):
        return min(items, key=lambda x: abs(x - pivot))

    return df_CHESS_numTests_regroup.loc[
        nearest(
            df_CHESS_numTests_regroup.index, pd.to_datetime(realTime, format="%Y-%m-%d")
        )
    ]


# Symptom parameters
# ------------------

# Estimating the baseline ILI-symptoms from earlier studies as well as the success rate of COVID-19 tests

# ILI rate estimate from 2018-19 PHE Surveillance of influenza and other respiratory viruses in the UK report:
# https://assets.publishing.service.gov.uk/government/uploads/system/uploads/attachment_data/file/839350/Surveillance_of_influenza_and_other_respiratory_viruses_in_the_UK_2018_to_2019-FINAL.pdf

# TODO - get actual seasonal symptom rate predictions (from 2020 non-SARS respiratory viruses, this data surely exists)
# (daily rate estimate from Figure 8 of the report)

# Respiratory diagnosis on hospital admissions (not just ILI, all, TODO - get only ILI?)
# NHS Hosp episode statistics 2018-19, page 12 https://files.digital.nhs.uk/F2/E70669/hosp-epis-stat-admi-summ-rep-2018-19-rep.pdf
# In hospital: 1.1 million respiratory episodes out of 17.1 million total episodes


def f_symptoms_nonCOVID(
    realTime,
    symptomsIliRCGP=15.0
    / 100000.0,  # Symptom rate in general non-hospitalised population
    symptomsRespInHospitalFAEs=1.1 / 17.1,  # Symptom rate in hospitalised population
    **kwargs,
):
    """
    This function defines the non-COVID ILI symptoms rate in the population at a given t time
    """

    # TODO, add extra data etc as input. For now:
    return (symptomsIliRCGP, symptomsRespInHospitalFAEs)


# Distribute tests amongst (a given subset of) symptomatic people
def distTestsSymp(
    people,
    testsAvailable,
    noncovid_sympRatio,
    symp_HS=range(3, 5),
    alreadyTestedRate=None,
):
    """
    distribute tests amongst symptomatic people
    people is nAge x nHS-1 x ... (excluding dead)
    """

    # Calculate noncovid, but symptomatic people
    peopleSymp = copy.deepcopy(people)
    peopleSymp[:, : min(symp_HS)] *= noncovid_sympRatio
    peopleSymp[:, max(symp_HS) :] *= noncovid_sympRatio

    # Subtract already tested people
    if alreadyTestedRate is not None:
        peopleSymp -= people * alreadyTestedRate

    # Check if we already tested everyone with a different test
    if np.sum(peopleSymp) < 1e-6:  # avoid numerical instabilities
        return (0.0, 0.0)

    testedRatio = min(1.0, testsAvailable / np.sum(peopleSymp))

    return (
        # test rate
        testedRatio * (peopleSymp / (people + 1e-6)),  # avoid dividing by zero
        # tests used to achieve this
        testedRatio * np.sum(peopleSymp),
    )


# Testing policies (how to distribute available tests)
# ----------------------------------------------------

# Estimate at any one time how many people are getting tested (with which tests) from which health states
def policyFunc_testing_symptomaticOnly(
    stateTensor,
    realTime,
    # Test types (names correspoding to testSpecifications)
    testTypes,  # = ["PCR", "Antigen", "Antibody"],
    # Test Capacity (dict with names above and numbers available on day t)
    testsAvailable,  # = trFunc_testCapacity(t)
    # OPTIONAL ARGUMENTS (may be different for different policy functions, should come with defaults!)
    antibody_testing_policy="hospworker_then_random",
    # This has these values (for now), {"none", "hospworker_then_random", "virus_positive_only", "virus_positive_only_hospworker_first"}
    # Baseline symptoms
    f_symptoms_nonCOVID=f_symptoms_nonCOVID,
    distributeRemainingToRandom=True,
    return_testsAvailable_remaining=False,
    **kwargs,
):
    """
    Returns a rate distribution of available test types over age, health and isolation states
    (although age assumed not to matter here)
    """

    # Output nAge x nHS x nIso x nTest x len(testTypes) tensor
    out_testRate = np.zeros(stateTensor.shape + (len(testTypes),))

    # Testing capacity is testsAvailable

    # Get sympom ratio. [0] - general, [1] - hospitalised
    cur_noncovid_sympRatio = f_symptoms_nonCOVID(
        realTime, **kwargs["f_symptoms_nonCOVID_params"]
    )

    # PCR testing
    # -----------

    # Hospitalised people get priority over PCR tests
    testRate, testsUsed = distTestsSymp(
        people=stateTensor[
            :, :-1, 2, 0
        ],  # hospitalised non-positive people, exclude tested and dead people
        testsAvailable=testsAvailable["PCR"],
        noncovid_sympRatio=cur_noncovid_sympRatio[1],
    )

    out_testRate[:, :-1, 2, 0, testTypes.index("PCR")] += testRate
    testsAvailable["PCR"] -= testsUsed

    # Prioritise hospital workers next:
    # TODO: check if we should do this? In UK policy there was a 15% max for hospital worker testing until ~2 April...
    testRate, testsUsed = distTestsSymp(
        people=stateTensor[:, :-1, 3, 0],
        testsAvailable=testsAvailable["PCR"],
        noncovid_sympRatio=cur_noncovid_sympRatio[0],
    )

    out_testRate[:, :-1, 3, 0, testTypes.index("PCR")] += testRate
    testsAvailable["PCR"] -= testsUsed

    # Distribute PCRs left over the other populations
    testRate, testsUsed = distTestsSymp(
        people=stateTensor[:, :-1, :2, 0],
        testsAvailable=testsAvailable["PCR"],
        noncovid_sympRatio=cur_noncovid_sympRatio[0],
    )

    out_testRate[:, :-1, :2, 0, testTypes.index("PCR")] += testRate
    testsAvailable["PCR"] -= testsUsed

    if distributeRemainingToRandom:
        # Distribute PCRs left over the other populations
        testRate, testsUsed = distTestsSymp(
            people=stateTensor[:, :-1, :, 0],
            testsAvailable=testsAvailable["PCR"],
            noncovid_sympRatio=1.0,
            alreadyTestedRate=out_testRate[:, :-1, :, 0, testTypes.index("PCR")],
        )

        out_testRate[:, :-1, :, 0, testTypes.index("PCR")] += testRate
        testsAvailable["PCR"] -= testsUsed

    # Antigen testing
    # ---------------

    # Hospitalised people get priority over PCR tests
    testRate, testsUsed = distTestsSymp(
        people=stateTensor[
            :, :-1, 2, 0
        ],  # hospitalised non-positive people, exclude tested and dead people
        testsAvailable=testsAvailable["Antigen"],
        noncovid_sympRatio=cur_noncovid_sympRatio[1],
        alreadyTestedRate=out_testRate[:, :-1, 2, 0, testTypes.index("PCR")],
    )

    out_testRate[:, :-1, 2, 0, testTypes.index("Antigen")] += testRate
    testsAvailable["Antigen"] -= testsUsed

    # Prioritise hospital workers next:
    # TODO: check if we should do this? In UK policy there was a 15% max for hospital worker testing until ~2 April...
    testRate, testsUsed = distTestsSymp(
        people=stateTensor[:, :-1, 3, 0],
        testsAvailable=testsAvailable["Antigen"],
        noncovid_sympRatio=cur_noncovid_sympRatio[0],
        alreadyTestedRate=out_testRate[:, :-1, 3, 0, testTypes.index("PCR")],
    )

    out_testRate[:, :-1, 3, 0, testTypes.index("Antigen")] += testRate
    testsAvailable["Antigen"] -= testsUsed

    # Distribute Antigen tests left over the other symptomatic people
    testRate, testsUsed = distTestsSymp(
        people=stateTensor[:, :-1, :2, 0],
        testsAvailable=testsAvailable["Antigen"],
        noncovid_sympRatio=cur_noncovid_sympRatio[0],
        alreadyTestedRate=out_testRate[:, :-1, :2, 0, testTypes.index("PCR")],
    )

    out_testRate[:, :-1, :2, 0, testTypes.index("Antigen")] += testRate
    testsAvailable["Antigen"] -= testsUsed

    if distributeRemainingToRandom:
        # Distribute antigen tests left over the other non-symptmatic populations
        testRate, testsUsed = distTestsSymp(
            people=stateTensor[:, :-1, :, 0],
            testsAvailable=testsAvailable["Antigen"],
            noncovid_sympRatio=1.0,
            alreadyTestedRate=out_testRate[:, :-1, :, 0, :].sum(-1),
        )

        out_testRate[:, :-1, :, 0, testTypes.index("Antigen")] += testRate
        testsAvailable["Antigen"] -= testsUsed

    # Antibody testing
    # ----------------

    if antibody_testing_policy == "hospworker_then_random":

        # For now: give to hospital workers first, not taking into account previous tests or symptoms
        testRate, testsUsed = distTestsSymp(
            people=stateTensor[:, :-1, 3, :2],
            testsAvailable=testsAvailable["Antibody"],
            noncovid_sympRatio=1.0,  # basically workers get antibody tested regardless of symptoms
        )

        out_testRate[:, :-1, 3, :2, testTypes.index("Antibody")] += testRate
        testsAvailable["Antibody"] -= testsUsed

        # Afterwards let's just distribute randomly in the rest of the population
        testRate, testsUsed = distTestsSymp(
            people=stateTensor[:, :-1, :3, :2],
            testsAvailable=testsAvailable["Antibody"],
            noncovid_sympRatio=1.0,  # basically people get antibody tested regardless of symptoms
        )

        out_testRate[:, :-1, :3, :2, testTypes.index("Antibody")] += testRate
        testsAvailable["Antibody"] -= testsUsed

    if antibody_testing_policy == "virus_positive_only_hospworker_first":

        # For now: give to hospital workers first, not taking into account previous tests or symptoms
        testRate, testsUsed = distTestsSymp(
            people=stateTensor[:, :-1, 3, 1],
            testsAvailable=testsAvailable["Antibody"],
            noncovid_sympRatio=1.0,  # basically workers get antibody tested regardless of symptoms
        )

        out_testRate[:, :-1, 3, 1, testTypes.index("Antibody")] += testRate
        testsAvailable["Antibody"] -= testsUsed

        # Afterwards let's just distribute randomly in the rest of the population
        # TODO: Maybe prioratise people who tested positive for the virus before???
        testRate, testsUsed = distTestsSymp(
            people=stateTensor[:, :-1, :3, 1],
            testsAvailable=testsAvailable["Antibody"],
            noncovid_sympRatio=1.0,  # basically people get antibody tested regardless of symptoms
        )

        out_testRate[:, :-1, :3, 1, testTypes.index("Antibody")] += testRate
        testsAvailable["Antibody"] -= testsUsed

    if antibody_testing_policy == "virus_positive_only":

        testRate, testsUsed = distTestsSymp(
            people=stateTensor[:, :-1, :, 1],
            testsAvailable=testsAvailable["Antibody"],
            noncovid_sympRatio=1.0,  # basically people get antibody tested regardless of symptoms
        )

        out_testRate[:, :-1, :, 1, testTypes.index("Antibody")] += testRate
        testsAvailable["Antibody"] -= testsUsed

    if antibody_testing_policy == "none":
        out_testRate += 0.0
        testsAvailable["Antibody"] -= 0.0

    if return_testsAvailable_remaining:
        return out_testRate, testsAvailable

    return out_testRate


# Define reTesting policy(s) (ie give tests to people in non-0 test states!)
def policyFunc_testing_massTesting_with_reTesting(
    stateTensor,
    realTime,
    # Test types (names correspoding to testSpecifications)
    testTypes,  # = ["PCR", "Antigen", "Antibody"],
    # Test Capacity (dict with names above and numbers available on day t)
    testsAvailable,  # = trFunc_testCapacity(t)
    # OPTIONAL ARGUMENTS (may be different for different policy functions, should come with defaults!)
    basic_policyFunc=policyFunc_testing_symptomaticOnly,
    # This basic policy will:
    # - do PCRs on symptomatic hospitalised people
    # - do PCRs on symptomatic hospital staff
    # - do PCRs on symptomatic non-hospitalised people
    # If PCRs run out at any stage, we use antigen tests with same priorisation
    # Afterwards given fractions of remaining antigen tests are distributed amongst people given these ratios and their earlier testing status:
    # retesting_antigen_viruspos_ratio = 0.1, # find virus false positives
    # UPDATE <- retesting viruspos is same ratio is normal testing, as long as they're not in quarantine already!
    retesting_antigen_immunepos_ratio=0.05,  # find immunity false positives
    # The rest of antigen tests are given out randomly
    # Antibody tests are used primarily on people who tested positive for the virus
    #  (set in basic_policyFunc!, use "virus_positive_only_hospworker_first"!)
    # Afterwards we can use the remaining on either random people (dangerous with many false positives!)
    # or for retesting people with already positive immune tests to make sure they're still immune,
    # controlled by this ratio:
    retesting_antibody_immunepos_ratio=1.0,
    # distributeRemainingToRandom = True, # TODO - otherwise stockpile for future, how?
    return_testsAvailable_remaining=False,
    **kwargs,
):

    # Output nAge x nHS x nIso x nTest x len(testTypes) tensor
    out_testRate = np.zeros(stateTensor.shape + (len(testTypes),))

    # First distribute tests to symptomatic people as usual:

    # inpArgs change to not distributing tests randomly:
    basic_policyFunc_params_modified = copy.deepcopy(kwargs["basic_policyFunc_params"])
    basic_policyFunc_params_modified["distributeRemainingToRandom"] = False
    basic_policyFunc_params_modified["return_testsAvailable_remaining"] = True

    # Run the basic policy function with these modified parameters
    out_testRate, testsAvailable = basic_policyFunc(
        stateTensor,
        realTime=realTime,
        testTypes=testTypes,
        testsAvailable=testsAvailable,
        **basic_policyFunc_params_modified,
    )

    # We assume PCRs tend to run out done on symptomatic people in 0 Test state, so no retesting via PCR.

    # Antigen testing
    # ---------------

    # Retesting immune positive people
    testRate, testsUsed = distTestsSymp(
        people=stateTensor[:, :-1, :, 2:],  # immune positive people
        testsAvailable=testsAvailable["Antigen"] * retesting_antigen_immunepos_ratio,
        noncovid_sympRatio=1.0,  # set to 1. for ignoring symptom vs non-symptom
    )

    out_testRate[:, :-1, :, 2:, testTypes.index("Antigen")] += testRate
    testsAvailable["Antigen"] -= testsUsed

    # Distribute antigen tests left over the other non-symptmatic populations
    # UPDATE <- here we use tests equally distributed among people with negative or positive previous virus tests,
    # as long as they are in non-quarantined state (isoState 0) # TODO - hospital worker testing???
    testRate, testsUsed = distTestsSymp(
        people=stateTensor[:, :-1, 0, :2],  # non-quarantined virus positive people
        testsAvailable=testsAvailable["Antigen"],
        noncovid_sympRatio=1.0,
        alreadyTestedRate=out_testRate[:, :-1, 0, :2, testTypes.index("Antigen")]
        + out_testRate[:, :-1, 0, :2, testTypes.index("PCR")],
    )

    out_testRate[:, :-1, 0, :2, testTypes.index("Antigen")] += testRate
    testsAvailable["Antigen"] -= testsUsed

    # Antibody testing
    # -----------------
    # Retesting antibody positive people
    testRate, testsUsed = distTestsSymp(
        people=stateTensor[:, :-1, :, 2:],  # virus positive people
        testsAvailable=testsAvailable["Antibody"] * retesting_antibody_immunepos_ratio,
        noncovid_sympRatio=1.0,  # set to 1. for ignoring symptom vs non-symptom
    )

    # Afterwards let's just distribute randomly in the rest of the population
    testRate, testsUsed = distTestsSymp(
        people=stateTensor[:, :-1, :, :2],
        testsAvailable=testsAvailable["Antibody"],
        noncovid_sympRatio=1.0,  # basically people get antibody tested regardless of symptoms
        alreadyTestedRate=out_testRate[:, :-1, :, :2, testTypes.index("Antibody")],
    )

    out_testRate[:, :-1, :, :2, testTypes.index("Antibody")] += testRate
    testsAvailable["Antibody"] -= testsUsed

    if return_testsAvailable_remaining:
        return out_testRate, testsAvailable

    return out_testRate


def trFunc_testing(
    stateTensor,
    t,
    realStartDate,
    #policyFunc = policyFunc_testing_symptomaticOnly,
    policyFunc=policyFunc_testing_massTesting_with_reTesting,
    inpFunc_testSpecifications=inpFunc_testSpecifications,
    trFunc_testCapacity=trFunc_testCapacity,
    inpFunc_realData_testCapacity=inpFunc_testingDataCHESS_PCR,
    **kwargs,
):
    """
    Returns a tensor of rates transitioning to tested states
    """
    trTensor_testTransitions = np.zeros((nAge, nHS, nIso, nTest, nTest))

    testSpecifications = inpFunc_testSpecifications(
        **kwargs["inpFunc_testSpecifications_params"]
    )

    testTypes = list(set(testSpecifications["Name"]))

    # Check if we have real data on the administered tests

    # Add the current data on within-hospital PCRs carried out already
    curDate = pd.to_datetime(realStartDate, format="%Y-%m-%d") + pd.to_timedelta(
        int(t), unit="D"
    )
    realData_closest = inpFunc_realData_testCapacity(
        realTime=curDate, **kwargs["inpFunc_realData_testCapacity_params"]
    )

    if realData_closest.name == curDate:  # We do have data, just fill it in
        testsAdministeredRate = np.zeros(stateTensor.shape + (len(testTypes),))

        # TODO - fix this very hacky solution accessing symptomatic ratio as a subfunc of the policy func
        noncovid_sympRatio = kwargs["policyFunc_params"]["basic_policyFunc_params"]["f_symptoms_nonCOVID"](curDate, **kwargs["policyFunc_params"]["basic_policyFunc_params"]["f_symptoms_nonCOVID_params"])

        noncovid_sympRatio = noncovid_sympRatio[1]  # Use hospitalised patient symptom ratio
        symptomaticRatePerDiseaseState = np.array([noncovid_sympRatio] * stateTensor.shape[1])
        symptomaticRatePerDiseaseState[3 : -(nR + 1)] = 1.0  # set the symptomatic ratio of symptomatic states to 1
        symptomaticPeoplePerDiseaseStateInHospital = stateTensor[:, :-1, 2, 0] * np.expand_dims(symptomaticRatePerDiseaseState[:-1], axis=0)

        testsAdministeredRate[:, :-1, 2, 0, testTypes.index("PCR")] += (
            np.expand_dims(
                realData_closest.to_numpy(), 1
            )  # true number of tests on given day per age group
            * (
                symptomaticPeoplePerDiseaseStateInHospital
                / np.sum(
                    symptomaticPeoplePerDiseaseStateInHospital, axis=-1, keepdims=True
                )
            )
            # Calculate in what ratio we distribute the tests to people along disease states based on symptomatic (age is given in data!)
        ) / (
            stateTensor[:, :-1, 2, 0] + 1e-10
        )  # Divide by total people in each state to get testing rate

    else:  # we don't have data, follow our assumed availability and policy curves

        # policyFunc returns stateTensor x testTypes tensor of test administration rates
        testsAdministeredRate = policyFunc(
            stateTensor,
            realTime=curDate,
            testTypes=testTypes,
            testsAvailable=trFunc_testCapacity(
                realTime=curDate, **kwargs["trFunc_testCapacity_params"]
            ),
            **kwargs["policyFunc_params"],
        )

    # Compute the transition ratio to tested states, given the administered tests

    for testType in testTypes:
        # Get the appropriate slices from testsAdmin. and testSpecs
        curTestSpecs = testSpecifications[testSpecifications["Name"] == testType]

        for curTS in range(nTest):
            # Set output positive test state based on current test state
            if curTS == int(curTestSpecs["OutputTestState"].values[0]):
                # already positive for the given test
                outTS_pos = curTS
            elif curTS == 3:
                # If already positive for both, stay positive
                outTS_pos = 3
            else:
                # Transition 0->1, 0->2, 1->2, 1->3 or 2->3
                outTS_pos = curTS + int(curTestSpecs["OutputTestState"].values[0])

            # Where do we go after negative test based on where we are now?
            if curTS == 0:
                # Negatives stay negatives
                outTS_neg = 0
            elif curTS == 3:
                # go to only virus or antibody positive from both positive
                outTS_neg = 3 - int(curTestSpecs["OutputTestState"].values[0])
            elif curTS == int(curTestSpecs["OutputTestState"].values[0]):
                # go to 0 if tested for the one you're positive for
                outTS_neg = 0
            else:
                # stay where you are if you test negative for the one you didnt have anyway
                outTS_neg = curTS

            # Get the transition rates based on current health states
            for curHS in range(nHS):
                # Add the true positives * (1-FNR)
                if curHS in curTestSpecs["TruePosHealthState"].values[0]:
                    trTensor_testTransitions[
                        :, curHS, :, curTS, outTS_pos
                    ] += testsAdministeredRate[
                        :, curHS, :, curTS, testTypes.index(testType)
                    ] * (
                        1
                        - curTestSpecs[curTestSpecs["InputHealthState"] == curHS][
                            "FalseNegativeRate"
                        ].values[0]
                    )

                else:
                    # Add the false positives * FPR
                    trTensor_testTransitions[:, curHS, :, curTS, outTS_pos] += (
                        testsAdministeredRate[
                            :, curHS, :, curTS, testTypes.index(testType)
                        ]
                        * curTestSpecs[curTestSpecs["InputHealthState"] == curHS][
                            "FalsePositiveRate"
                        ].values[0]
                    )

                # Add the false negatives (FNR)
                if curHS in curTestSpecs["TruePosHealthState"].values[0]:
                    trTensor_testTransitions[:, curHS, :, curTS, outTS_neg] += (
                        testsAdministeredRate[
                            :, curHS, :, curTS, testTypes.index(testType)
                        ]
                        * curTestSpecs[curTestSpecs["InputHealthState"] == curHS][
                            "FalseNegativeRate"
                        ].values[0]
                    )

                else:
                    # Add the true negatives * (1-FNR)
                    trTensor_testTransitions[:, curHS, :, curTS, outTS_neg] += (
                        testsAdministeredRate[
                            :, curHS, :, curTS, testTypes.index(testType)
                        ]
                        * curTestSpecs[curTestSpecs["InputHealthState"] == curHS][
                            "FalsePositiveRate"
                        ].values[0]
                    )

    return trTensor_testTransitions  # , testsAdministeredRate


# ## Quarantine policies
#
# This section describes alternatives to the social distancing by full lockdown (that is implemented as a change in the socialMixing matrices).
#
# One alternative is case isolation, either by hospitalisation or by home isolation. We will assume that all non-symptomatic people who test
# positive are home isolated along with families for
# nDaysInIsolation days. Symptomatic people have a chance of being immediately hospitalised instead of sent into home isolation
def trFunc_quarantine_caseIsolation(
    trTensor_complete,
    t,
    trTensor_testing,  # This is used to establish who gets tests and how many of those end up positive.
    nDaysInHomeIsolation=nDaysInHomeIsolation,
    timeToIsolation=0.5,  # (days) time from testing positive to actually getting isolated
    # On average this many people get hospitalised (compared to home isolation), but modulated by age (TODO: values > 1? clip for now..)
    symptomHospitalisedRate_ageAdjusted=np.clip(
        adjustRatesByAge_KeepAverageRate(
            0.3, ageRelativeAdjustment=relativeAdmissionRisk_given_COVID_by_age
        ),
        0.0,
        1.0,
    ),
    symptomaticHealthStates=[
        3,
        4,
    ],  # TODO - define this in global variable and just pass here!
    **kwargs,
):
    """
    This function redistributes testing rates, so they dont only create a testing state update, but also an isolation state update
    """
    trTensor_quarantineRate = np.zeros(stateTensor.shape + (nIso,))

    trTensor_freshlyVirusPositiveRate_inIso0 = copy.deepcopy(
        trTensor_testing[:, :, 0, :2, 1]
    )
    trTensor_freshlyBothPositiveRate_inIso0 = copy.deepcopy(
        trTensor_testing[:, :, 0, 2:, 3]
    )

    for curHS in range(stateTensor.shape[1] - 1):  # ignore dead
        if curHS in symptomaticHealthStates:
            # Send a fraction of people (normal) who are symptomatic and tested positive to hospital, based on their age
            trTensor_quarantineRate[:, curHS, 0, :2, 2] += (
                (1.0 / timeToIsolation)
                * symptomHospitalisedRate_ageAdjusted[:, np.newaxis]
                * trTensor_freshlyVirusPositiveRate_inIso0[:, curHS]
            )
            trTensor_quarantineRate[:, curHS, 0, 2:, 2] += (
                (1.0 / timeToIsolation)
                * symptomHospitalisedRate_ageAdjusted[:, np.newaxis]
                * trTensor_freshlyBothPositiveRate_inIso0[:, curHS]
            )
            # The rest to home isolation
            trTensor_quarantineRate[:, curHS, 0, :2, 1] += (
                (1.0 / timeToIsolation)
                * (1.0 - symptomHospitalisedRate_ageAdjusted[:, np.newaxis])
                * trTensor_freshlyVirusPositiveRate_inIso0[:, curHS]
            )
            trTensor_quarantineRate[:, curHS, 0, 2:, 1] += (
                (1.0 / timeToIsolation)
                * (1.0 - symptomHospitalisedRate_ageAdjusted[:, np.newaxis])
                * trTensor_freshlyBothPositiveRate_inIso0[:, curHS]
            )

        else:
            # Send all non-symptomatic (normal) who tested freshly positive to home isolation
            trTensor_quarantineRate[:, curHS, 0, :2, 1] += (
                1.0
                / timeToIsolation
                * trTensor_freshlyVirusPositiveRate_inIso0[:, curHS]
            )
            trTensor_quarantineRate[:, curHS, 0, 2:, 1] += (
                1.0
                / timeToIsolation
                * trTensor_freshlyBothPositiveRate_inIso0[:, curHS]
            )

    # Release people from home isolation after isolation period
    trTensor_quarantineRate[:, :, 1, :, 0] = 1.0 / nDaysInHomeIsolation

    # Hospitalised people are assumed to be released after recovery, with normal rates (TODO: think if this is correct)

    # TODO!!! - importantly, hospital workers are not being home isolated / hospitalised under this policy.
    # How to keep track of hospital workers who get hospitalised or home isolated themselves,
    # such that they get back to being hospital workers afterwards?
    # A simple (slightly incorrect) solution would be to just implement a non-specific "pull" from isoState=0 people to hospital workers to fill up the missing people?
    # But the rate of this pull would be impossible to compute and would still be incorrect. Gotta think more on this.

    # Update the whole tensor accordingly
    # Make a copy for safety:
    out_trTensor_complete = copy.deepcopy(trTensor_complete)

    # First remove all the iso 0->0, test 0,1->1, 2,3->3 transitions (as they're all either hospitalised or sent to home isolation)
    out_trTensor_complete[:, :, 0, :2, :, 0, 1] = 0.0
    out_trTensor_complete[:, :, 0, 2:, :, 0, 3] = 0.0

    # Newly virus positive, newly home-isolated, diagonal in disease state transition
    np.einsum("ijkj->ijk", out_trTensor_complete[:, :, 0, :2, :, 1, 1])[
        :
    ] = trTensor_quarantineRate[:, :, 0, :2, 1]
    np.einsum("ijkj->ijk", out_trTensor_complete[:, :, 0, 2:, :, 1, 3])[
        :
    ] = trTensor_quarantineRate[:, :, 0, 2:, 1]

    # Newly virus positive, newly hospitalised, diagonal in disease state transition
    np.einsum("ijkj->ijk", out_trTensor_complete[:, :, 0, :2, :, 2, 1])[
        :
    ] = trTensor_quarantineRate[:, :, 0, :2, 2]
    np.einsum("ijkj->ijk", out_trTensor_complete[:, :, 0, 2:, :, 2, 3])[
        :
    ] = trTensor_quarantineRate[:, :, 0, 2:, 2]

    # Home isolated people are "let go" after nDaysInHomeIsolation, without changing disease or testing state
    # (TODO: represent multiple testing / needing negative tests to let go, etc - hard problem!)
    # (UPDATE: multiple testing have now been represented, but for now we'll still let go people based on fixed time rather than negative test, to save tests!)
    np.einsum("ijkjk->ijk", out_trTensor_complete[:, :, 1, :, :, 0, :])[
        :
    ] = trTensor_quarantineRate[:, :, 1, :, 0]

    # Return the full updated tensor (so NOT += outside, but actually =)
    return out_trTensor_complete


# ## Full simulation function
# Function that computes the right side of the non-lin model ODE
def dydt_Complete(
    t,
    stateTensor_flattened,  # Might be double the normal size (as first dimension) _withNewOnlyCopy, if debugReturnNewPerDay
    realStartDate = testingStartDate,
    #realStartDate=pd.to_datetime("2020-02-20", format="%Y-%m-%d"),
    # debug
    debugTransition=False,
    debugTimestep=False,
    debugReturnNewPerDay=True,  # Now implemented by default into state iteration
    # Dimensions
    nAge=nAge,
    nHS=nHS,
    nI=nI,
    nR=nR,
    nIso=nIso,
    nTest=nTest,
    # Input functions and tensors
    # ----------------------------
    
    # Health state updates
    trFunc_diseaseProgression=trFunc_diseaseProgression,
    trFunc_newInfections=trFunc_newInfections_Complete,
    
    # Initial incoming travel-based infections (before restrictions)
    trFunc_travelInfectionRate_ageAdjusted=trFunc_travelInfectionRate_ageAdjusted,
    
    # Hospitalisation and recovery
    trFunc_HospitalAdmission=trFunc_HospitalAdmission,
    trFunc_HospitalDischarge=trFunc_HospitalDischarge,
    
    # Policy changes (on social distancing for now) (TODO - possibly make more changes)
    tStartSocialDistancing=tStartSocialDistancing,
    tStopSocialDistancing=tStopSocialDistancing,
    tStartImmunityPassports=tStartImmunityPassports,
    tStopImmunityPassports=tStopImmunityPassports,
    tStartQuarantineCaseIsolation=tStartQuarantineCaseIsolation,
    tStopQuarantineCaseIsolation=tStopQuarantineCaseIsolation,
    trFunc_quarantine=trFunc_quarantine_caseIsolation,
    
    # Testing
    trFunc_testing=trFunc_testing,
    # policyFunc_testing = policyFunc_testing_symptomaticOnly,
    # testSpecifications = testSpecifications,
    # trFunc_testCapacity = trFunc_testCapacity,
    # trFunc_testCapacity_param_testCapacity_antigenratio_country = 0.3
    **kwargs,
):

    # print out sim day while running
    tt = round(t,)
    used = []
    if tt % 5 == 0:
        if t not in used:
            print(f" Sim Day: {tt}", end="\r")
            used.append(t)

    if debugTimestep:
        print(t)

    # Initialise return
    if (
        debugReturnNewPerDay
    ):  # the input has 2 copies of the state tensor, second copy being the cumulative incomings
        stateTensor = np.reshape(stateTensor_flattened, [2, nAge, nHS, nIso, nTest])[0]
    else:
        stateTensor = np.reshape(stateTensor_flattened, [nAge, nHS, nIso, nTest])

    dydt = np.zeros_like(stateTensor)

    # Initialise the full transition tensor
    trTensor_complete = np.zeros((nAge, nHS, nIso, nTest, nHS, nIso, nTest))

    # Disease condition updates
    # ---------------------------
    trTensor_diseaseProgression = trFunc_diseaseProgression(
        **kwargs["trFunc_diseaseProgression_params"]
    )

    # Get disease condition updates with no isolation or test transition ("diagonal along those")
    for k1 in [0, 1, 2, 3]:
        np.einsum("ijlml->ijlm", trTensor_complete[:, :, k1, :, :, k1, :])[
            :
        ] += np.expand_dims(
            trTensor_diseaseProgression[:, :, k1, :], [2]
        )  # all non-hospitalised disease progression is same

    # Compute new infections (0->1 in HS) with no isolation or test transition ("diagonal along those")
    cur_policySocialDistancing = (
        t >= (tStartSocialDistancing - realStartDate).days
    ) * (t < (tStopSocialDistancing - realStartDate).days)
    cur_policyImmunityPassports = (
        t >= (tStartImmunityPassports - realStartDate).days
    ) * (t < (tStopImmunityPassports - realStartDate).days)
    np.einsum("iklkl->ikl", trTensor_complete[:, 0, :, :, 1, :, :])[
        :
    ] += trFunc_newInfections(
        stateTensor,
        policySocialDistancing=cur_policySocialDistancing,
        policyImmunityPassports=cur_policyImmunityPassports,
        **kwargs["trFunc_newInfections_params"],
    )

    # Also add new infected from travelling of healthy people, based on time-within-simulation (this is correct with all (0,0) states, as tested or isolated people dont travel)
    trTensor_complete[:, 0, 0, 0, 1, 0, 0] += trFunc_travelInfectionRate_ageAdjusted(
        t, **kwargs["trFunc_travelInfectionRate_ageAdjusted_params"]
    )

    # Hospitalisation state updates
    # -----------------------

    # Hospitalisation and recovery rates
    # We assume for now that these only depend on age and disease progression, not on testing state
    # (TODO - update this given new policies)

    # The disease and testing states don't change due to hospitalisation.
    # Hospital staff is treated as already hospitalised from all aspects expect social mixing, should suffice for now
    # TODO - Could try to devise a scheme in which hospital staff gets hospitalised and some recoveries from hospitalised state go back to hospital staff.
    # TODO - same issue with hospital staff home isolating; that's probably more important question!
    for k1 in [0, 1]:
        np.einsum("ijljl->ijl", trTensor_complete[:, :, k1, :, :, 2, :])[
            :
        ] += np.expand_dims(
            trFunc_HospitalAdmission(**kwargs["trFunc_HospitalAdmission_params"]), [2]
        )

    # Add recovery from hospital rates
    # TODO - again here (for now) we assume all discharged people go back to "normal state" instead of home isolation, have to think more on this
    np.einsum("ijljl->ijl", trTensor_complete[:, :, 2, :, :, 0, :])[
        :
    ] += np.expand_dims(
        trFunc_HospitalDischarge(**kwargs["trFunc_HospitalDischarge_params"]), [2]
    )

    # Testing state updates
    # ---------------------

    # trFunc_testing returns a stateTensor x testStates output
    #      after the policyFunc assigns tests that are evaluated according to testSpecifications

    # Diagonal (no transitions) in age, health state and isolation state
    # (for now, probably TODO: testing positive correlates with new hospitalisation!)
    trTensor_testing = trFunc_testing(
        stateTensor, t, realStartDate, **kwargs["trFunc_testing_params"]
    )

    np.einsum("ijkljkm->ijklm", trTensor_complete)[:] += trTensor_testing

    # Quarantine policy
    # ------------------

    # Check if policy is "on"
    if (t >= (tStartQuarantineCaseIsolation - realStartDate).days) * (
        t < (tStopQuarantineCaseIsolation - realStartDate).days
    ):
        # New quarantining only happens to people who are transitioning already from untested to virus positive state
        # Therefore here we DO use non-diagonal transitions, and we
        #     redistribute the transtion rates given the testing (which was previously assumed not to create transition in isolation state)
        trTensor_complete = trFunc_quarantine(
            trTensor_complete, t, trTensor_testing, **kwargs["trFunc_quarantine_params"]
        )

    # Final corrections
    # -----------------

    # TODO: simulate aging and normal birth / death (not terribly important on these time scales, but should be quite simple)

    # Ensure that every "row" sums to 0 by adding to the diagonal (doesn't create new people out of nowhere)
    # Extract (writable) diagonal array and subtract the "row"-sums for each initial state
    np.einsum("ijkljkl->ijkl", trTensor_complete)[:] -= np.einsum(
        "...jkl->...", trTensor_complete
    )

    # Compute the actual derivatives
    dydt = np.einsum(
        "ijkl,ijklmnp->imnp", stateTensor, trTensor_complete
    )  # contract the HS axis, keep age

    if debugReturnNewPerDay:
        """
        If this is true, instead of returning the real dydt,
        return only the positive "incoming" number of people to each state, so we can track "new cases"
        This needs some approximations, as follows:
            1. Take the normal transition tensor (with rates potentially > 0)
            2. From all states re-normalise the outgoing rates to sum at most to 1
                (if they were less, keep it, if larger, then this represents
                “in this day, all people will leave this state, in these ratios to these states”)
            3. Multiply only these outgoing rates with the current state
                (so the result wont keep the same number of people as normal,
                but only represent the “new incomings” for each state)
        """

        trTensor_complete_newOnly = copy.deepcopy(trTensor_complete)

        # TODO - Think - this is probably unnecessary actually, artifically reduces "new" rates?
        #         # Devide each row by the absolute diagonal rate (that is the sum of the row), but only if its larger than 1
        #         trTensor_complete_newOnly /= (
        #             np.expand_dims(
        #                 np.clip(np.abs(np.einsum('ijkljkl->ijkl', trTensor_complete_newOnly)), a_min=1., a_max=np.inf),
        #                 axis=[4,5,6]
        #             )
        #         )

        # Set the diagonals to zero (no preservation, no outgoing, will end up being the incoming only)
        np.einsum("ijkljkl->ijkl", trTensor_complete_newOnly)[:] = 0.0

        dydt_newOnly = np.einsum(
            "ijkl,ijklmnp->imnp", stateTensor, trTensor_complete_newOnly
        )

        dydt = np.stack([dydt, dydt_newOnly], axis=0)

    if debugTransition:
        return np.reshape(dydt, -1), trTensor_complete

    return np.reshape(dydt, -1)


def solveSystem(stateTensor_init, total_days, samplesPerDay=np.inf, **kwargs):
    # Run the simulation
    if kwargs["debugReturnNewPerDay"]:  # Keep the second copy as well
        cur_stateTensor = np.reshape(
            np.stack(
                [copy.deepcopy(stateTensor_init), copy.deepcopy(stateTensor_init)],
                axis=0,
            ),
            -1,
        )
    else:
        # print("else 1")
        cur_stateTensor = np.reshape(copy.deepcopy(stateTensor_init), -1)

    if np.isinf(samplesPerDay):
        # print("if 2")
        # Run precise integrator - used for all simulations
        out = integrate.solve_ivp(
            fun=lambda t, y: dydt_Complete(t, y, **kwargs),
            t_span=(0.0, total_days),
            y0=cur_stateTensor,
            method="RK23",
            t_eval=range(total_days),
            rtol=1e-3,  # default 1e-3
            atol=1e-3,  # default 1e-6
        )
        # print(out)
        out = out.y

    else:
        # print("else 2")
        # Run simple Euler method with given step size (1/samplesPerDay) for quickly investigating code behavior
        deltaT = 1.0 / samplesPerDay
        out = np.zeros((np.prod(stateTensor_init.shape), total_days))

        for tt in range(total_days * samplesPerDay):
            if tt % samplesPerDay == 0:
                out[:, int(tt / samplesPerDay)] = cur_stateTensor

            cur_stateTensor += deltaT * dydt_Complete(
                (tt * 1.0) / (1.0 * samplesPerDay), cur_stateTensor, **kwargs
            )

    # Reshape to reasonable format
    if kwargs["debugReturnNewPerDay"]:
        out = np.reshape(out, (2,) + stateTensor_init.shape + (-1,))
    else:
        out = np.reshape(out, stateTensor_init.shape + (-1,))

    return out

### df Clean up for folding on all states except Health States
def array_to_df(total_days, result):
    
    reshape = 2*nAge*nHS*nIso*nTest*total_days
    sim_days = [x+1 for x in range(total_days)]

    iterables=[['current','new'],
               ["0-9","10-19","20-29","30-39","40-49","50-59","60-69","70-79","80+"],
               ["susceptible", "exposed", "asymptomatic", "infected1", "infected2", "recovered1", "recovered2", "deceased"],
               ['distancing','quarantined','hospitalized','hospStaff'],
               ["neg_noTest", "pos_test","pos_antibody", "pos_both"],
               sim_days
          ]
    index = pd.MultiIndex.from_product(iterables, names=['arrivalType','ageGroup','healthState','isoState','testState','simDay'])

    df = pd.DataFrame(result.reshape(reshape, 1),index=index).stack().reset_index().rename(columns={0: "value"})

    #delete phantom column "level_6"
    for col in df.columns.to_list():
        if "level" in col:
            del df[col]
    
    df = df.groupby(['simDay','arrivalType', 'ageGroup', 'healthState',], as_index=False)["value"].sum()
    
    return df

# convert int simday to datetime
def num_to_date(testingStartDate, simDay):
    temp_date = testingStartDate + timedelta(days=simDay)
    return str(temp_date.date())

# call num_to_date and reorder columns
def clean_df(df):
    df['timestamp'] = df.simDay.apply(lambda x: num_to_date(testingStartDate, x)) 

    ts = df['timestamp']
    df.drop(labels=['timestamp'], axis=1,inplace = True)
    df.insert(0, 'timestamp', ts)

    return df    

if __name__ == "__main__":

    print("\n")
    start_it = datetime.now()
    print(f"Started at {start_it}")
    print("Running model...")

    # # Build a dictionary out of arguments with defaults
    paramDict_default = build_paramDict(dydt_Complete)
    paramDict_default["dydt_Complete"] = dydt_Complete
    paramDict_default["INIT_stateTensor_init"] = stateTensor_init

    paramDict_current = copy.deepcopy(paramDict_default)

    result = solveSystem(stateTensor_init, total_days, **paramDict_current)

    df = clean_df(array_to_df(total_days, result))

    print(df.tail())
    df.to_csv(f"{workdir}/results/{outfile}", index=False)
    
    end_it = datetime.now()
    print(f"Runtime = {end_it-start_it}")
    print("\n")
    print(f"Results written to {workdir}/results/{outfile}")
    print("\n")



