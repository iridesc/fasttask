from fasttask_manager.manager import Manager


for i in range(1, 101):
    r =  Manager(
        "127.0.0.1",
        port="9001",
        task_name="get_circle_area",
        protocol="https",
    ).create_task(
        {
            "r": i,
        }
    )
    print(r)

for a, b in [(i, i) for i in range(1, 101)]:
    Manager(
        "127.0.0.1",
        port="9001",
        task_name="get_hypotenuse",
        protocol="https",
    ).create_task(
        {
            "a": 3,
            "b": 4,
        }
    )
