FROM kbase/sdkbase2:python
MAINTAINER michael.t.shaffer@colostate.edu
# -----------------------------------------
# In this section, you can install any system dependencies required
# to run your App.  For instance, you could place an apt-get update or
# install line here, a git checkout to download code, or run any other
# installation scripts.

# Install miniconda
RUN apt-get update && \
    apt-get install wget -q && \
    wget -nv https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh && \
    sh Miniconda3-latest-Linux-x86_64.sh -b -p /root/miniconda
# Add miniconda bin to path permanently in the container
ENV PATH=/root/miniconda/bin:$PATH
# Set up conda
RUN echo $(which conda) && \
    conda config --set always_yes yes && \
    conda config --add channels bioconda && \
    conda config --add channels conda-forge
# Install dependencies and DRAM
# install from conda
#RUN wget -nv https://raw.githubusercontent.com/shafferm/DRAM/master/environment.yaml && \
#    conda install -q pyyaml && \
#    conda env update -n base --file environment.yaml
#RUN pip install jsonrpcbase
# install from pip
RUN conda install -q pandas scikit-bio prodigal "mmseqs2!=10.6d92c" "hmmer!=3.3.1" "trnascan-se >=2" sqlalchemy barrnap "altair >=4" openpyxl networkx ruby parallel wget nose coverage pyyaml git && \
    pip install -q 'DRAM-bio>=1.2.3' jsonrpcbase && \
    git clone https://github.com/WrightonLabCSU/DRAM.git && \
    cd DRAM && \
    pip install .
# install from github
#RUN conda install -q pandas scikit-bio prodigal "mmseqs2!=10.6d92c" "hmmer!=3.3.1" "trnascan-se >=2" sqlalchemy barrnap "altair >=4" openpyxl networkx ruby parallel wget nose coverage pyyaml && \
#    pip install -q jsonrpcbase && \
#    git clone https://github.com/shafferm/DRAM.git && \
#    cd DRAM && \
#    pip install .
# -----------------------------------------

COPY ./ /kb/module
RUN mkdir -p /kb/module/work
RUN chmod -R a+rw /kb/module

WORKDIR /kb/module

RUN make all

ENTRYPOINT [ "./scripts/entrypoint.sh" ]

CMD [ ]
