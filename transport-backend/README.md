to run mock frontend:
run api.py
go to http://localhost:5005











Router:
- Run main.py in terminal
- Waiting to be connected to front-end
- Lacking train data
- Assumes to deals with bus and walking, including overnight buses, operational days, etc.
- Output seems correct, but requires more tests

Bus Live:
- Run bus_live.py in terminal
- Waiting to be connected to front-end
- Input lat, lon should be the mid-point of the map-window 
- Should add function of getting mid-point and updating every 5 seconds after connected to front-end

Router and Bus Live should be concurrent, maybe using thread.