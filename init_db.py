from app import create_app
from models import db, User, Location, Material, Batch, Category
from datetime import date

app = create_app()

with app.app_context():
    db.drop_all()
    db.create_all()

    # ── ПОЛЬЗОВАТЕЛИ (все роли) ──
    users_data = [
        ('admin', 'admin123', 'Администратор', 'admin'),
        ('zaved', 'zaved123', 'Заведующий отделением', 'head'),
        ('sestra', 'sestra123', 'Старшая медсестра', 'head_nurse'),
        ('vrach_sklad', 'vrach123', 'Врач-кладовщик', 'doctor_storekeeper'),
        ('vrach', 'vrach123', 'Врач-оператор', 'doctor'),
        ('xray', 'xray123', 'Рентгенлаборант', 'xray_lab'),
    ]
    for username, password, full_name, role in users_data:
        user = User(username=username, full_name=full_name, role=role)
        user.set_password(password)
        db.session.add(user)

    # ── КАТЕГОРИИ ──
    categories_data = [
        ('Баллоны коронарные', 'Баллонные катетеры для коронарной ангиопластики'),
        ('Проводники', 'Коронарные проводники'),
        ('Катетеры диагностические', 'Диагностические катетеры'),
        ('Наборы ангиографические', 'Наборы для ангиографии'),
        ('Прочее', 'Остальные расходные материалы'),
        ('Повторное использование', 'Материалы после стерилизации'),
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
        ('podval', 'Подвал'),
        ('mat', 'Материальная'),
        ('oper', 'Операционная'),
    ]:
        loc = Location(code=code, description=desc)
        db.session.add(loc)
        locations[code] = loc
    db.session.flush()

    # ── МАТЕРИАЛЫ И ПАРТИИ ──
    materials_data = [
        ('TIG 2', '5F', '2027-01-01', 100, 91, 'podval', 'Катетеры диагностические'),
        ('Merit Ultimate 1', '5F', '2028-03-01', 180, 164, 'podval', 'Катетеры диагностические'),
        ('JR 4 130', '5F', '2028-05-01', 30, 15, 'mat', 'Катетеры диагностические'),
        ('PIG 130', '5F', '2028-05-01', 15, 4, 'mat', 'Катетеры диагностические'),
        ('CBR', '5F', '2028-05-01', 10, 1, 'oper', 'Баллоны коронарные'),
        ('Набор ангиографический Veismed', '6F', '2028-12-01', 30, 28, 'mat', 'Наборы ангиографические'),
    ]

    for name, size, expiry_str, qty, used, loc_code, cat_name in materials_data:
        category = categories.get(cat_name)
        material = Material(name=name, size=size, category_id=category.id if category else None)
        db.session.add(material)
        db.session.flush()
        expiry_date = date.fromisoformat(expiry_str)
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
    print("База данных создана!")
    print("=" * 60)
    print("Пользователи:")
    print("  admin       / admin123   (Администратор)")
    print("  zaved       / zaved123   (Заведующий)")
    print("  sestra      / sestra123  (Старшая медсестра)")
    print("  vrach_sklad / vrach123   (Врач-кладовщик)")
    print("  vrach       / vrach123   (Врач)")
    print("  xray        / xray123    (Рентгенлаборант)")
    print("=" * 60)
    print(f"Категорий: {Category.query.count()}")
    print(f"Материалов: {Material.query.count()}")
    print(f"Партий: {Batch.query.count()}")
    print(f"Локаций: {Location.query.count()}")