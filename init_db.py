from app import create_app
from models import db, User, Location, Material, Batch, Category
from datetime import date

app = create_app()

with app.app_context():
    db.drop_all()
    db.create_all()

    # ── ПОЛЬЗОВАТЕЛИ ──
    admin = User(username='admin', full_name='Администратор', role='admin')
    admin.set_password('admin123')

    storekeeper = User(username='ivanov', full_name='Иванов И.И.', role='storekeeper')
    storekeeper.set_password('store123')

    viewer = User(username='petrov', full_name='Петров П.П.', role='viewer')
    viewer.set_password('view123')

    db.session.add_all([admin, storekeeper, viewer])

    # ── КАТЕГОРИИ ──
    categories_data = [
        ('Баллоны коронарные', 'Баллонные катетеры для коронарной ангиопластики'),
        ('Проводники', 'Коронарные проводники'),
        ('Катетеры диагностические', 'Диагностические катетеры'),
        ('Наборы ангиографические', 'Наборы для ангиографии'),
        ('Прочее', 'Остальные расходные материалы'),
    ]
    categories = {}
    for name, desc in categories_data:
        cat = Category(name=name, description=desc)
        db.session.add(cat)
        categories[name] = cat

    db.session.flush()

    # ── МЕСТА ХРАНЕНИЯ ──
    locations = {}
    for code, desc in [
        ('110', 'Основной склад'),
        ('340', 'Склад №2'),
        ('0', 'Основная ячейка'),
        ('10', 'Ячейка 10'),
        ('15', 'Ячейка 15'),
        ('30', 'Ячейка 30'),
        ('105', 'Ячейка 105'),
        ('610', 'Ячейка 610'),
    ]:
        loc = Location(code=code, description=desc)
        db.session.add(loc)
        locations[code] = loc

    db.session.flush()

    # ── МАТЕРИАЛЫ И ПАРТИИ ──
    materials_data = [
        ('TIG 2', '5F', '2027-01-01', 100, 91, 9, '110', 'Катетеры диагностические'),
        ('Merit Ultimate 1', '5F', '2028-03-01', 180, 164, 16, '340', 'Катетеры диагностические'),
        ('JR 4 130', '5F', '2028-05-01', 30, 15, 15, None, 'Катетеры диагностические'),
        ('PIG 130', '5F', '2028-05-01', 15, 4, 11, None, 'Катетеры диагностические'),
        ('PIG A', '5F', '2028-05-01', 5, 2, 3, '10', 'Катетеры диагностические'),
        ('CBR', '5F', '2028-05-01', 10, 1, 9, '0', 'Баллоны коронарные'),
        ('CBL', '5F', '2028-05-01', 5, 1, 4, '0', 'Баллоны коронарные'),
        ('IM', '5F', '2028-05-01', 15, 10, 5, '0', 'Катетеры диагностические'),
        ('JR 5.0', '5F', '2028-05-01', 15, 1, 14, '0', 'Катетеры диагностические'),
        ('AR 1', '5F', '2028-05-01', 10, 3, 7, '0', 'Катетеры диагностические'),
        ('AR 2', '5F', '2028-05-01', 5, 1, 4, '0', 'Катетеры диагностические'),
        ('JR 4.0', '5F', '2028-05-01', 105, 93, 12, '30', 'Катетеры диагностические'),
        ('JL 4.0', '5F', '2028-05-01', 90, 69, 21, '15', 'Катетеры диагностические'),
        ('AL 1', '5F', '2028-05-01', 10, 2, 8, '0', 'Катетеры диагностические'),
        ('UAC (маточный ангиог. катетер)', '5F', '2028-07-01', 15, 0, 15, None, 'Прочее'),
        ('Набор ангиографический Veismed', '6F', '2028-12-01', 30, 28, 2, '105', 'Наборы ангиографические'),
    ]

    for name, size, expiry_str, qty, used, remaining, loc_code, cat_name in materials_data:
        category = categories.get(cat_name)
        material = Material(name=name, size=size, category_id=category.id if category else None)
        db.session.add(material)
        db.session.flush()

        expiry_date = date.fromisoformat(expiry_str) if expiry_str else None
        location_id = locations[loc_code].id if loc_code else None

        batch = Batch(
            material_id=material.id,
            location_id=location_id,
            expiry_date=expiry_date,
            quantity=qty,
            used=used,
        )
        db.session.add(batch)

    db.session.commit()

    print("=" * 60)
    print("База данных создана и заполнена тестовыми данными!")
    print("=" * 60)
    print("Пользователи для входа:")
    print("  admin    / admin123  (администратор)")
    print("  ivanov   / store123  (кладовщик)")
    print("  petrov   / view123   (наблюдатель)")
    print("=" * 60)
    print(f"Категорий создано: {Category.query.count()}")
    print(f"Материалов создано: {Material.query.count()}")
    print(f"Партий создано: {Batch.query.count()}")
    print(f"Локаций создано: {Location.query.count()}")