FROM irid/fasttask

# set up your docker env here if necessary
# ---

# ---

COPY req.txt req.txt
RUN pip install -r req.txt
RUN rm -rf tasks
COPY tasks tasks
