from flask import Flask, render_template, redirect, url_for, flash, request
from config import Config
from models import db, User, Material, Batch, Location, Transaction, Category
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from datetime import date, datetime

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.login_message = 'Пожалуйста, войдите для доступа.'


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # ──────────────────────────────────────────
    # АВТОРИЗАЦИЯ
    # ──────────────────────────────────────────
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for('index'))

        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')

            user = User.query.filter_by(username=username).first()

            if user and user.check_password(password):
                if user.is_active:
                    login_user(user)
                    flash(f'Добро пожаловать, {user.full_name or user.username}!', 'success')
                    next_page = request.args.get('next')
                    return redirect(next_page or url_for('index'))
                else:
                    flash('Ваша учётная запись отключена.', 'danger')
            else:
                flash('Неверное имя пользователя или пароль.', 'danger')

        return render_template('login.html')

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        flash('Вы вышли из системы.', 'info')
        return redirect(url_for('login'))

    # ──────────────────────────────────────────
    # ГЛАВНАЯ СТРАНИЦА (дашборд)
    # ──────────────────────────────────────────
    @app.route('/')
    @login_required
    def index():
        today = date.today()

        batches = Batch.query.filter(
            Batch.is_active == True,
            Batch.quantity - Batch.used > 0
        ).order_by(Batch.expiry_date.asc()).all()

        total_materials = Material.query.count()
        total_batches = Batch.query.filter(Batch.is_active == True).count()
        expired_batches = Batch.query.filter(
            Batch.is_active == True,
            Batch.quantity - Batch.used > 0,
            Batch.expiry_date < today
        ).count()

        expiring_soon = Batch.query.filter(
            Batch.is_active == True,
            Batch.quantity - Batch.used > 0,
            Batch.expiry_date >= today,
            Batch.expiry_date <= db.func.date(today, '+90 days')
        ).count()

        return render_template('index.html',
                               batches=batches,
                               total_materials=total_materials,
                               total_batches=total_batches,
                               expired_batches=expired_batches,
                               expiring_soon=expiring_soon,
                               today=today)

    # ──────────────────────────────────────────
    # ДЕТАЛИЗАЦИЯ ПРОСРОЧЕННЫХ И ИСТЕКАЮЩИХ
    # ──────────────────────────────────────────
    @app.route('/dashboard/expired')
    @login_required
    def dashboard_expired():
        today = date.today()
        batches = Batch.query.filter(
            Batch.is_active == True,
            Batch.quantity - Batch.used > 0,
            Batch.expiry_date < today
        ).order_by(Batch.expiry_date.asc()).all()

        return render_template('dashboard_filtered.html',
                               batches=batches,
                               title='Просроченные позиции',
                               filter_type='expired')

    @app.route('/dashboard/expiring')
    @login_required
    def dashboard_expiring():
        today = date.today()
        batches = Batch.query.filter(
            Batch.is_active == True,
            Batch.quantity - Batch.used > 0,
            Batch.expiry_date >= today,
            Batch.expiry_date <= db.func.date(today, '+90 days')
        ).order_by(Batch.expiry_date.asc()).all()

        return render_template('dashboard_filtered.html',
                               batches=batches,
                               title='Истекают в течение 90 дней',
                               filter_type='expiring')

    # ──────────────────────────────────────────
    # МАТЕРИАЛЫ (справочник)
    # ──────────────────────────────────────────
    @app.route('/materials')
    @login_required
    def materials():
        search = request.args.get('search', '').strip()
        category_id = request.args.get('category_id', '').strip()

        query = Material.query
        if search:
            query = query.filter(
                Material.name.ilike(f'%{search}%') |
                Material.size.ilike(f'%{search}%') |
                Material.barcode.ilike(f'%{search}%')
            )
        if category_id:
            query = query.filter(Material.category_id == int(category_id))

        materials_list = query.order_by(Material.name).all()
        categories_list = Category.query.order_by(Category.name).all()
        return render_template('materials.html',
                               materials=materials_list,
                               search=search,
                               categories=categories_list,
                               selected_category=category_id)

    @app.route('/materials/add', methods=['GET', 'POST'])
    @login_required
    def material_add():
        if not current_user.is_storekeeper():
            flash('Недостаточно прав для добавления материалов.', 'danger')
            return redirect(url_for('materials'))

        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            size = request.form.get('size', '').strip()
            barcode = request.form.get('barcode', '').strip()
            category_id = request.form.get('category_id', '').strip()
            min_stock = request.form.get('min_stock', '0').strip()
            note = request.form.get('note', '').strip()

            if not name:
                flash('Название обязательно!', 'danger')
                categories_list = Category.query.order_by(Category.name).all()
                return render_template('material_form.html', material=None, categories=categories_list)

            material = Material(
                name=name,
                size=size if size else None,
                barcode=barcode if barcode else None,
                category_id=int(category_id) if category_id else None,
                min_stock=int(min_stock) if min_stock.isdigit() else 0,
                note=note if note else None
            )
            db.session.add(material)
            db.session.commit()
            flash(f'Материал "{name}" добавлен!', 'success')
            return redirect(url_for('materials'))

        categories_list = Category.query.order_by(Category.name).all()
        return render_template('material_form.html', material=None, categories=categories_list)

    @app.route('/materials/<int:id>/edit', methods=['GET', 'POST'])
    @login_required
    def material_edit(id):
        if not current_user.is_storekeeper():
            flash('Недостаточно прав для редактирования.', 'danger')
            return redirect(url_for('materials'))

        material = Material.query.get_or_404(id)

        if request.method == 'POST':
            material.name = request.form.get('name', '').strip()
            material.size = request.form.get('size', '').strip() or None
            material.barcode = request.form.get('barcode', '').strip() or None
            cat_id = request.form.get('category_id', '').strip()
            material.category_id = int(cat_id) if cat_id else None
            min_stock = request.form.get('min_stock', '0').strip()
            material.min_stock = int(min_stock) if min_stock.isdigit() else 0
            material.note = request.form.get('note', '').strip() or None

            db.session.commit()
            flash(f'Материал "{material.name}" обновлён!', 'success')
            return redirect(url_for('materials'))

        categories_list = Category.query.order_by(Category.name).all()
        return render_template('material_form.html', material=material, categories=categories_list)

    @app.route('/materials/<int:id>/delete', methods=['POST'])
    @login_required
    def material_delete(id):
        if not current_user.is_admin():
            flash('Только администратор может удалять материалы.', 'danger')
            return redirect(url_for('materials'))

        material = Material.query.get_or_404(id)
        name = material.name

        for batch in material.batches:
            Transaction.query.filter_by(batch_id=batch.id).delete()
        Batch.query.filter_by(material_id=material.id).delete()
        db.session.delete(material)
        db.session.commit()

        flash(f'Материал "{name}" и все связанные записи удалены.', 'info')
        return redirect(url_for('materials'))

    # ──────────────────────────────────────────
    # КАТЕГОРИИ
    # ──────────────────────────────────────────
    @app.route('/categories')
    @login_required
    def categories():
        cats = Category.query.order_by(Category.name).all()
        return render_template('categories.html', categories=cats)

    @app.route('/categories/add', methods=['GET', 'POST'])
    @login_required
    def category_add():
        if not current_user.is_storekeeper():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('categories'))

        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()

            if not name:
                flash('Название категории обязательно!', 'danger')
                return render_template('category_form.html', category=None)

            if Category.query.filter_by(name=name).first():
                flash(f'Категория "{name}" уже существует!', 'danger')
                return render_template('category_form.html', category=None)

            cat = Category(name=name, description=description if description else None)
            db.session.add(cat)
            db.session.commit()
            flash(f'Категория "{name}" добавлена!', 'success')
            return redirect(url_for('categories'))

        return render_template('category_form.html', category=None)

    @app.route('/categories/<int:id>/edit', methods=['GET', 'POST'])
    @login_required
    def category_edit(id):
        if not current_user.is_storekeeper():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('categories'))

        cat = Category.query.get_or_404(id)

        if request.method == 'POST':
            new_name = request.form.get('name', '').strip()
            existing = Category.query.filter(Category.name == new_name, Category.id != id).first()
            if existing:
                flash(f'Категория "{new_name}" уже существует!', 'danger')
                return render_template('category_form.html', category=cat)

            cat.name = new_name
            cat.description = request.form.get('description', '').strip() or None
            db.session.commit()
            flash(f'Категория "{cat.name}" обновлена!', 'success')
            return redirect(url_for('categories'))

        return render_template('category_form.html', category=cat)

    @app.route('/categories/<int:id>/delete', methods=['POST'])
    @login_required
    def category_delete(id):
        if not current_user.is_admin():
            flash('Только администратор может удалять категории.', 'danger')
            return redirect(url_for('categories'))

        cat = Category.query.get_or_404(id)
        name = cat.name

        Material.query.filter_by(category_id=cat.id).update({Material.category_id: None})
        db.session.delete(cat)
        db.session.commit()

        flash(f'Категория "{name}" удалена. Материалы остались без категории.', 'info')
        return redirect(url_for('categories'))

    # ──────────────────────────────────────────
    # ЛОКАЦИИ (места хранения)
    # ──────────────────────────────────────────
    @app.route('/locations')
    @login_required
    def locations():
        locs = Location.query.order_by(Location.code).all()
        return render_template('locations.html', locations=locs)

    @app.route('/locations/add', methods=['GET', 'POST'])
    @login_required
    def location_add():
        if not current_user.is_storekeeper():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('locations'))

        if request.method == 'POST':
            code = request.form.get('code', '').strip()
            description = request.form.get('description', '').strip()

            if not code:
                flash('Код локации обязателен!', 'danger')
                return render_template('location_form.html', location=None)

            if Location.query.filter_by(code=code).first():
                flash(f'Локация с кодом "{code}" уже существует!', 'danger')
                return render_template('location_form.html', location=None)

            loc = Location(code=code, description=description if description else None)
            db.session.add(loc)
            db.session.commit()
            flash(f'Локация "{code}" добавлена!', 'success')
            return redirect(url_for('locations'))

        return render_template('location_form.html', location=None)

    @app.route('/locations/<int:id>/edit', methods=['GET', 'POST'])
    @login_required
    def location_edit(id):
        if not current_user.is_storekeeper():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('locations'))

        loc = Location.query.get_or_404(id)

        if request.method == 'POST':
            new_code = request.form.get('code', '').strip()
            existing = Location.query.filter(Location.code == new_code, Location.id != id).first()
            if existing:
                flash(f'Локация с кодом "{new_code}" уже существует!', 'danger')
                return render_template('location_form.html', location=loc)

            loc.code = new_code
            loc.description = request.form.get('description', '').strip() or None
            db.session.commit()
            flash(f'Локация "{loc.code}" обновлена!', 'success')
            return redirect(url_for('locations'))

        return render_template('location_form.html', location=loc)

    @app.route('/locations/<int:id>/delete', methods=['POST'])
    @login_required
    def location_delete(id):
        if not current_user.is_admin():
            flash('Только администратор может удалять локации.', 'danger')
            return redirect(url_for('locations'))

        loc = Location.query.get_or_404(id)
        code = loc.code

        Batch.query.filter_by(location_id=loc.id).update({Batch.location_id: None})
        db.session.delete(loc)
        db.session.commit()

        flash(f'Локация "{code}" удалена.', 'info')
        return redirect(url_for('locations'))

    # ──────────────────────────────────────────
    # СКАНЕР ШТРИХКОДОВ
    # ──────────────────────────────────────────
    @app.route('/scanner')
    @login_required
    def scanner():
        return render_template('scanner.html')

    @app.route('/api/material-by-barcode')
    @login_required
    def api_material_by_barcode():
        barcode = request.args.get('barcode', '').strip()

        if not barcode:
            return {'found': False, 'error': 'Штрихкод не указан'}

        material = Material.query.filter_by(barcode=barcode).first()

        if not material:
            return {'found': False}

        batches = Batch.query.filter(
            Batch.material_id == material.id,
            Batch.is_active == True,
            Batch.quantity - Batch.used > 0
        ).order_by(Batch.expiry_date.asc()).all()

        total_remaining = sum(b.remaining for b in batches)

        batches_html = ''
        if batches:
            batches_html = '<h6 class="mt-3">Партии:</h6><table class="table table-sm table-bordered"><thead><tr><th>Срок</th><th>Остаток</th><th>Локация</th></tr></thead><tbody>'
            for b in batches:
                expiry = b.expiry_date.strftime('%d.%m.%Y') if b.expiry_date else '—'
                loc = b.location.code if b.location else '—'
                batches_html += f'<tr><td>{expiry}</td><td>{b.remaining}</td><td>{loc}</td></tr>'
            batches_html += '</tbody></table>'

        return {
            'found': True,
            'material': {
                'id': material.id,
                'name': material.name,
                'size': material.size,
                'barcode': material.barcode,
                'category_id': material.category_id,
                'category_name': material.category_name,
            },
            'total_remaining': total_remaining,
            'batches_html': batches_html,
        }

    # ──────────────────────────────────────────
    # API: список категорий
    # ──────────────────────────────────────────
    @app.route('/api/categories')
    @login_required
    def api_categories():
        cats = Category.query.order_by(Category.name).all()
        return [{'id': c.id, 'name': c.name} for c in cats]

    # ──────────────────────────────────────────
    # ПРИХОД (IN)
    # ──────────────────────────────────────────
    @app.route('/operations/in', methods=['GET', 'POST'])
    @login_required
    def operation_in():
        if not current_user.is_storekeeper():
            flash('Недостаточно прав для прихода.', 'danger')
            return redirect(url_for('index'))

        material_id = request.args.get('material_id')
        selected_material = Material.query.get(material_id) if material_id else None

        if request.method == 'POST':
            mat_id = request.form.get('material_id', '').strip()
            qty = int(request.form.get('quantity', 0))
            expiry_str = request.form.get('expiry_date', '').strip()
            location_code = request.form.get('location_code', '').strip()
            batch_number = request.form.get('batch_number', '').strip()
            note = request.form.get('note', '').strip()
            new_name = request.form.get('new_material_name', '').strip()
            new_size = request.form.get('new_material_size', '').strip()
            new_barcode = request.form.get('new_material_barcode', '').strip()
            new_category_id = request.form.get('new_material_category_id', '').strip()

            if qty <= 0:
                flash('Укажите количество больше 0.', 'danger')
                return redirect(url_for('operation_in'))

            if mat_id and mat_id != 'new':
                material = Material.query.get_or_404(int(mat_id))
            elif new_name:
                material = Material.query.filter_by(name=new_name, size=new_size if new_size else None).first()
                if not material:
                    material = Material(
                        name=new_name,
                        size=new_size if new_size else None,
                        barcode=new_barcode if new_barcode else None,
                        category_id=int(new_category_id) if new_category_id else None,
                    )
                    db.session.add(material)
                    db.session.flush()
            else:
                flash('Выберите существующий материал или введите название нового.', 'danger')
                return redirect(url_for('operation_in'))

            location = None
            if location_code:
                location = Location.query.filter_by(code=location_code).first()

            expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date() if expiry_str else None
            batch = Batch(
                material_id=material.id,
                location_id=location.id if location else None,
                batch_number=batch_number if batch_number else None,
                expiry_date=expiry_date,
                quantity=qty,
                used=0
            )
            db.session.add(batch)
            db.session.flush()

            transaction = Transaction(
                user_id=current_user.id,
                batch_id=batch.id,
                type='in',
                quantity=qty,
                note=note if note else f'Приход материала "{material.name}"'
            )
            db.session.add(transaction)
            db.session.commit()

            flash(f'Приход: {material.name} — {qty} шт. добавлено!', 'success')
            return redirect(url_for('index'))

        materials_list = Material.query.order_by(Material.name).all()
        locations_list = Location.query.order_by(Location.code).all()
        categories_list = Category.query.order_by(Category.name).all()
        return render_template('operation_in.html',
                               materials=materials_list,
                               locations=locations_list,
                               categories=categories_list,
                               selected_material=selected_material)

    # ──────────────────────────────────────────
    # РАСХОД (OUT)
    # ──────────────────────────────────────────
    @app.route('/operations/out', methods=['GET', 'POST'])
    @login_required
    def operation_out():
        if not current_user.is_storekeeper():
            flash('Недостаточно прав для расхода.', 'danger')
            return redirect(url_for('index'))

        material_id = request.args.get('material_id')
        selected_material = Material.query.get(material_id) if material_id else None

        if request.method == 'POST':
            mat_id = request.form.get('material_id')
            qty = int(request.form.get('quantity', 0))
            note = request.form.get('note', '').strip()

            if not mat_id or qty <= 0:
                flash('Выберите материал и укажите количество больше 0.', 'danger')
                return redirect(url_for('operation_out'))

            material = Material.query.get_or_404(mat_id)

            batches = Batch.query.filter(
                Batch.material_id == material.id,
                Batch.is_active == True,
                Batch.quantity - Batch.used > 0
            ).order_by(Batch.expiry_date.asc()).all()

            total_available = sum(b.remaining for b in batches)
            if qty > total_available:
                flash(f'Недостаточно остатков! Доступно: {total_available} шт.', 'danger')
                materials_list = Material.query.order_by(Material.name).all()
                categories_list = Category.query.order_by(Category.name).all()
                return render_template('operation_out.html',
                                       materials=materials_list,
                                       categories=categories_list,
                                       selected_material=selected_material)

            remaining_to_take = qty
            for batch in batches:
                if remaining_to_take <= 0:
                    break
                available = batch.remaining
                take = min(available, remaining_to_take)
                batch.used += take
                remaining_to_take -= take

                transaction = Transaction(
                    user_id=current_user.id,
                    batch_id=batch.id,
                    type='out',
                    quantity=take,
                    note=note if note else f'Расход материала "{material.name}"'
                )
                db.session.add(transaction)

            db.session.commit()
            flash(f'Расход: {material.name} — {qty} шт. списано!', 'success')
            return redirect(url_for('index'))

        materials_list = Material.query.order_by(Material.name).all()
        categories_list = Category.query.order_by(Category.name).all()
        return render_template('operation_out.html',
                               materials=materials_list,
                               categories=categories_list,
                               selected_material=selected_material)

    # ──────────────────────────────────────────
    # ЖУРНАЛ ОПЕРАЦИЙ
    # ──────────────────────────────────────────
    @app.route('/transactions')
    @login_required
    def transactions():
        page = request.args.get('page', 1, type=int)
        transactions_list = Transaction.query.order_by(
            Transaction.created_at.desc()
        ).limit(100).all()
        return render_template('transactions.html', transactions=transactions_list)

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5050)