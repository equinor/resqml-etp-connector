# Resqml ETP Cconnection experiment
Python code to demonstrate creation and transfer of a RESQML object using Energistics Transfer Protocol (ETP).
The code does the following:
* convert a xtgeo .gri file to a RESQML Grid2dRepresentation object using [resqpy](https://github.com/bp/resqpy)
* upload this RESQML object to a OSDU/RDDMS server using the ETP protocol. This uses a [modified version of the Geosiris ETP stack](https://github.com/equinor/etpclient-python/tree/modifications_for_Grid2d_test) (etpclient-python, etpproto-python, etptypes).
* download the RESQML object from the RDDMS server using the ETP protocol.  This uses the same modified version of the Geosiris ETP stack (etpclient-python, etpproto-python, etptypes).
* read the downloaded data using resqpy to complete the data round-trip.
* assert that the round-tripped data is unchanged.

## Setup
The setup sequence below is to be verified:
- install resqpy using pip
- install the requirements of etpclient-python
    - etpclient-python uses poetry. One option is to manually pip-install its requirements(?)
- obtain the patched version of etpclient-python and add it to PYTHONPATH
    - The patches are hosted in the [Equinor fork of etpclient-python](https://github.com/equinor/etpclient-python/tree/modifications_for_Grid2d_test).
    - Add the patched etpclient-python in front of your PYTHONPATH to prefer it over any existing installed version.

## Run the code
Locate a xtgeo .gri file to be used in the test.  Update the input file .gri location at the top of the example code file resqpy_grid2d_roundtrip.py.  Run the code in resqpy_grid2d_roundtrip.py and verify the individual steps manually. 
