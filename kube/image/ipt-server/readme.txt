sudo apt-get update
sudo apt-get install libcap2-bin

create venv
#set caps to allow unpriviledged route modifications for debugging
x=`which python` && rm -f $x && cp -f /usr/bin/python3 $x && sudo setcap cap_net_admin+ep $x


#todo: tune packet cache in powerdns.
#todo: modify SOA so caching will be no longer than X seconds
