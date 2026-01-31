#!/usr/bin/env python3
"""
Transport Backend - Main Entry Point
Initializes the transit routing system and provides access to all components
"""

from atco import Atco
from timetable import Timetable
from walking import Walking
from raptorRouter import RaptorRouter


def initialize_system():
    """Initialize all components of the transit system"""
    print("=" * 60)
    print("Initializing Transport Backend System")
    print("=" * 60)
    
    # Initialize ATCO stops database
    print("\n[1/3] Initializing ATCO stops database...")
    atco = Atco()
    atco.initialize()
    stop_count = atco.get_stops_count()
    bus_count = len(atco.get_bus_stops())
    train_count = len(atco.get_train_stops())
    print(f"✓ Loaded {stop_count} stops ({bus_count} bus, {train_count} train)")
    
    # Initialize Timetable
    print("\n[2/3] Initializing Timetable...")
    timetable = Timetable("bus")
    stops = timetable.get_stops()
    print(f"✓ Timetable ready ({len(stops)} bus stops)")
    
    # Initialize Walking
    print("\n[3/3] Initializing Walking connectivity...")
    walking = Walking()
    print("✓ Walking module ready")
    
    print("\n" + "=" * 60)
    print("System Initialization Complete!")
    print("=" * 60)
    
    return {
        'atco': atco,
        'timetable': timetable,
        'walking': walking,
        'router': RaptorRouter()
    }


def show_menu():
    """Display main menu"""
    print("\nTransport Backend - Main Menu")
    print("-" * 40)
    print("1. Show ATCO stops statistics")
    print("2. Look up a stop by ATCO code")
    print("3. Get gazetteer ID for a stop")
    print("4. Exit")
    print("-" * 40)


def main():
    """Main entry point"""
    # Initialize system
    system = initialize_system()
    
    atco = system['atco']
    
    # Interactive menu
    while True:
        show_menu()
        choice = input("\nSelect option (1-4): ").strip()
        
        if choice == "1":
            # Show statistics
            all_stops = atco.get_all_stops()
            bus_stops = atco.get_bus_stops()
            train_stops = atco.get_train_stops()
            print(f"\n{'Total stops:':<20} {len(all_stops)}")
            print(f"{'Bus stops:':<20} {len(bus_stops)}")
            print(f"{'Train stops:':<20} {len(train_stops)}")
        
        elif choice == "2":
            # Look up a stop
            atco_code = input("Enter ATCO code: ").strip()
            stop = atco.get_stop(atco_code)
            if stop:
                print(f"\nStop: {stop['name']}")
                print(f"  ATCO Code: {stop['atco_code']}")
                print(f"  Type: {stop['stop_type']}")
                print(f"  Gazetteer ID: {stop['gazzetteer_id']}")
                print(f"  Coordinates: ({stop['lat']}, {stop['lon']})")
            else:
                print(f"Stop '{atco_code}' not found")
        
        elif choice == "3":
            # Get gazetteer ID
            atco_code = input("Enter ATCO code: ").strip()
            gazetteer_id = atco.get_gazetteer_id(atco_code)
            if gazetteer_id:
                print(f"Gazetteer ID for {atco_code}: {gazetteer_id}")
            else:
                print(f"Stop '{atco_code}' not found or has no gazetteer ID")
        
        elif choice == "4":
            print("Exiting...")
            break
        
        else:
            print("Invalid option. Please try again.")


if __name__ == "__main__":
    main()
