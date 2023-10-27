# Resqml ETP Connection experiment
Python code to demonstrate creation and transfer of a RESQML object using Energistics Transfer Protocol (ETP).
The code does the following:
* convert a `xtgeo` `.gri`-file to a RESQML Grid2dRepresentation object using [resqpy](https://github.com/bp/resqpy)
* upload this RESQML object to a OSDU/RDDMS server using the ETP protocol. This uses a [modified version of the Geosiris ETP stack](https://github.com/equinor/etpclient-python/tree/modifications_for_Grid2d_test) (etpclient-python, etpproto-python, etptypes).
* download the RESQML object from the RDDMS server using the ETP protocol.  This uses the same modified version of the Geosiris ETP stack (etpclient-python, etpproto-python, etptypes).
* read the downloaded data using resqpy to complete the data round-trip.
* assert that the round-tripped data is unchanged.


## Setup
To test this roundtrip locally do the following (assuming that some version of Python 3 and Docker is installed):
- Run `docker compose up --detach` to set up the postgres database and the the ETP-server on localhost (this runs the [`compose.yaml`](compose.yaml)-file in this directory) in the background. This should take around 10-30 seconds.
- Install the Python requirements with `pip install -r requirements.txt`. Alternatively, set up a virtual environment first before installing the requirements.


## Run the code
Locate a `xtgeo` `.gri`-file to be used in the test.
Update the input file `.gri` location at the top of the example code file `resqpy_grid2d_roundtrip.py` with the path and name of your local file.
Run the code via `python resqpy_grid2d_roundtrip.py` and verify the individual steps manually. 
