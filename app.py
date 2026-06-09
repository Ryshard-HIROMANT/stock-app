import os
from flask import Flask, render_template, redirect, url_for, flash, request
from config import Config
from models import db, User, Material, Batch, Location, Transaction, Category, Transfer, TransferItem, TransferRequest, SpendingDraft, Revision
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

    # Автосоздание таблиц при запуске
    with app.app_context():
        db.create_all()

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

        # Повторные материалы
        reusable_count = Material.query.filter_by(is_reusable=True).count()
        # Заявки на перемещение
        pending_requests = TransferRequest.query.filter_by(status='pending').count()
        # Неподтверждённые списания
        pending_drafts = SpendingDraft.query.filter_by(status='pending').count()

        return render_template('index.html',
                               batches=batches,
                               total_materials=total_materials,
                               total_batches=total_batches,
                               expired_batches=expired_batches,
                               expiring_soon=expiring_soon,
                               reusable_count=reusable_count,
                               pending_requests=pending_requests,
                               pending_drafts=pending_drafts,
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
                               batches=batches, title='Просроченные позиции', filter_type='expired')

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
                               batches=batches, title='Истекают в течение 90 дней', filter_type='expiring')

    # ──────────────────────────────────────────
    # МАТЕРИАЛЫ (справочник)
    # ──────────────────────────────────────────
    @app.route('/materials')
    @login_required
    def materials():
        search = request.args.get('search', '').strip()
        category_id = request.args.get('category_id', '').strip()
        reusable = request.args.get('reusable', '').strip()

        query = Material.query
        if search:
            query = query.filter(
                Material.name.ilike(f'%{search}%') |
                Material.size.ilike(f'%{search}%') |
                Material.barcode.ilike(f'%{search}%')
            )
        if category_id:
            query = query.filter(Material.category_id == int(category_id))
        if reusable == '1':
            query = query.filter(Material.is_reusable == True)
        elif reusable == '0':
            query = query.filter(Material.is_reusable == False)

        materials_list = query.order_by(Material.name).all()
        categories_list = Category.query.order_by(Category.name).all()
        return render_template('materials.html',
                               materials=materials_list, search=search,
                               categories=categories_list, selected_category=category_id)

    @app.route('/materials/add', methods=['GET', 'POST'])
    @login_required
    def material_add():
        if not current_user.is_storekeeper():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('materials'))

        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            size = request.form.get('size', '').strip()
            barcode = request.form.get('barcode', '').strip()
            category_id = request.form.get('category_id', '').strip()
            is_reusable = request.form.get('is_reusable') == '1'
            min_stock = request.form.get('min_stock', '0').strip()
            note = request.form.get('note', '').strip()

            if not name:
                flash('Название обязательно!', 'danger')
                categories_list = Category.query.order_by(Category.name).all()
                return render_template('material_form.html', material=None, categories=categories_list)

            material = Material(
                name=name, size=size if size else None,
                barcode=barcode if barcode else None,
                category_id=int(category_id) if category_id else None,
                is_reusable=is_reusable,
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
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('materials'))

        material = Material.query.get_or_404(id)

        if request.method == 'POST':
            material.name = request.form.get('name', '').strip()
            material.size = request.form.get('size', '').strip() or None
            material.barcode = request.form.get('barcode', '').strip() or None
            cat_id = request.form.get('category_id', '').strip()
            material.category_id = int(cat_id) if cat_id else None
            material.is_reusable = request.form.get('is_reusable') == '1'
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
            flash('Только администратор.', 'danger')
            return redirect(url_for('materials'))
        material = Material.query.get_or_404(id)
        name = material.name
        for batch in material.batches:
            Transaction.query.filter_by(batch_id=batch.id).delete()
        Batch.query.filter_by(material_id=material.id).delete()
        db.session.delete(material)
        db.session.commit()
        flash(f'Материал "{name}" удалён.', 'info')
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
                flash('Название обязательно!', 'danger')
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
            flash('Только администратор.', 'danger')
            return redirect(url_for('categories'))
        cat = Category.query.get_or_404(id)
        Material.query.filter_by(category_id=cat.id).update({Material.category_id: None})
        db.session.delete(cat)
        db.session.commit()
        flash(f'Категория "{cat.name}" удалена.', 'info')
        return redirect(url_for('categories'))

    # ──────────────────────────────────────────
    # ЛОКАЦИИ
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
                flash(f'Локация "{code}" уже существует!', 'danger')
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
                flash(f'Локация "{new_code}" уже существует!', 'danger')
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
            flash('Только администратор.', 'danger')
            return redirect(url_for('locations'))
        loc = Location.query.get_or_404(id)
        Batch.query.filter_by(location_id=loc.id).update({Batch.location_id: None})
        db.session.delete(loc)
        db.session.commit()
        flash(f'Локация "{loc.code}" удалена.', 'info')
        return redirect(url_for('locations'))

    # ──────────────────────────────────────────
    # СКАНЕР
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
            return {'found': False}
        material = Material.query.filter_by(barcode=barcode).first()
        if not material:
            return {'found': False}
        batches = Batch.query.filter(
            Batch.material_id == material.id, Batch.is_active == True,
            Batch.quantity - Batch.used > 0
        ).order_by(Batch.expiry_date.asc()).all()
        total_remaining = sum(b.remaining for b in batches)
        return {
            'found': True,
            'material': {
                'id': material.id, 'name': material.name, 'size': material.size,
                'barcode': material.barcode, 'category_id': material.category_id,
                'category_name': material.category_name, 'is_reusable': material.is_reusable,
            },
            'total_remaining': total_remaining,
        }

    @app.route('/api/categories')
    @login_required
    def api_categories():
        cats = Category.query.order_by(Category.name).all()
        return [{'id': c.id, 'name': c.name} for c in cats]

    # ──────────────────────────────────────────
    # ПРИХОД
    # ──────────────────────────────────────────
    @app.route('/operations/in', methods=['GET', 'POST'])
    @login_required
    def operation_in():
        if not current_user.is_storekeeper():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))

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
            is_reusable = request.form.get('is_reusable') == '1'

            if qty <= 0:
                flash('Укажите количество больше 0.', 'danger')
                return redirect(url_for('operation_in'))

            if mat_id and mat_id != 'new':
                material = Material.query.get_or_404(int(mat_id))
            elif new_name:
                material = Material.query.filter_by(name=new_name, size=new_size if new_size else None).first()
                if not material:
                    material = Material(
                        name=new_name, size=new_size if new_size else None,
                        barcode=new_barcode if new_barcode else None,
                        category_id=int(new_category_id) if new_category_id else None,
                        is_reusable=is_reusable,
                    )
                    db.session.add(material)
                    db.session.flush()
            else:
                flash('Выберите материал или введите название нового.', 'danger')
                return redirect(url_for('operation_in'))

            location = Location.query.filter_by(code=location_code).first() if location_code else None
            expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date() if expiry_str else None
            batch = Batch(
                material_id=material.id, location_id=location.id if location else None,
                batch_number=batch_number if batch_number else None,
                expiry_date=expiry_date, quantity=qty, used=0
            )
            db.session.add(batch)
            db.session.flush()

            t_type = 'reusable_in' if material.is_reusable else 'in'
            transaction = Transaction(
                user_id=current_user.id, batch_id=batch.id, type=t_type,
                quantity=qty, note=note if note else f'Приход "{material.name}"'
            )
            db.session.add(transaction)
            db.session.commit()
            flash(f'Приход: {material.name} — {qty} шт.!', 'success')
            return redirect(url_for('index'))

        materials_list = Material.query.order_by(Material.name).all()
        locations_list = Location.query.order_by(Location.code).all()
        categories_list = Category.query.order_by(Category.name).all()
        return render_template('operation_in.html',
                               materials=materials_list, locations=locations_list,
                               categories=categories_list, selected_material=None)

    # ──────────────────────────────────────────
    # РАСХОД
    # ──────────────────────────────────────────
    @app.route('/operations/out', methods=['GET', 'POST'])
    @login_required
    def operation_out():
        if not current_user.can_create_draft():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))

        if request.method == 'POST':
            mat_id = request.form.get('material_id')
            qty = int(request.form.get('quantity', 0))
            note = request.form.get('note', '').strip()
            operation_id = request.form.get('operation_id', '').strip()
            doctor_name = request.form.get('doctor_name', '').strip()

            if not mat_id or qty <= 0:
                flash('Выберите материал и укажите количество.', 'danger')
                return redirect(url_for('operation_out'))

            # Если врач или заведующий — списываем сразу
            if current_user.role in ('admin', 'head', 'doctor_storekeeper', 'doctor'):
                material = Material.query.get_or_404(mat_id)
                batches = Batch.query.filter(
                    Batch.material_id == material.id, Batch.is_active == True,
                    Batch.quantity - Batch.used > 0
                ).order_by(Batch.expiry_date.asc()).all()
                total_available = sum(b.remaining for b in batches)
                if qty > total_available:
                    flash(f'Недостаточно! Доступно: {total_available} шт.', 'danger')
                    return redirect(url_for('operation_out'))
                remaining_to_take = qty
                for batch in batches:
                    if remaining_to_take <= 0:
                        break
                    take = min(batch.remaining, remaining_to_take)
                    batch.used += take
                    remaining_to_take -= take
                    t_type = 'reusable_out' if material.is_reusable else 'out'
                    db.session.add(Transaction(
                        user_id=current_user.id, batch_id=batch.id, type=t_type,
                        quantity=take, operation_id=operation_id, doctor_name=doctor_name,
                        note=note or f'Расход "{material.name}"'
                    ))
                db.session.commit()
                flash(f'Расход: {material.name} — {qty} шт.!', 'success')
            else:
                # Лаборант создаёт черновик
                draft = SpendingDraft(
                    user_id=current_user.id, material_id=int(mat_id),
                    quantity=qty, operation_id=operation_id, doctor_name=doctor_name,
                    note=note
                )
                db.session.add(draft)
                db.session.commit()
                flash(f'Черновик списания создан. Ожидает подтверждения врачом.', 'info')

            return redirect(url_for('index'))

        materials_list = Material.query.order_by(Material.name).all()
        categories_list = Category.query.order_by(Category.name).all()
        return render_template('operation_out.html',
                               materials=materials_list, categories=categories_list,
                               selected_material=None)

    # ──────────────────────────────────────────
    # ПОДТВЕРЖДЕНИЕ СПИСАНИЙ (для врачей)
    # ──────────────────────────────────────────
    @app.route('/spending/confirm')
    @login_required
    def spending_confirm_list():
        if not current_user.can_confirm():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))
        drafts = SpendingDraft.query.filter_by(status='pending').order_by(SpendingDraft.created_at.desc()).all()
        return render_template('spending_confirm.html', drafts=drafts)

    @app.route('/spending/<int:id>/approve', methods=['POST'])
    @login_required
    def spending_approve(id):
        if not current_user.can_confirm():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))
        draft = SpendingDraft.query.get_or_404(id)
        # Списываем материал
        material = draft.material
        batches = Batch.query.filter(
            Batch.material_id == material.id, Batch.is_active == True,
            Batch.quantity - Batch.used > 0
        ).order_by(Batch.expiry_date.asc()).all()
        total_available = sum(b.remaining for b in batches)
        if draft.quantity > total_available:
            flash(f'Недостаточно! Доступно: {total_available} шт.', 'danger')
            return redirect(url_for('spending_confirm_list'))
        remaining_to_take = draft.quantity
        for batch in batches:
            if remaining_to_take <= 0:
                break
            take = min(batch.remaining, remaining_to_take)
            batch.used += take
            remaining_to_take -= take
            db.session.add(Transaction(
                user_id=current_user.id, batch_id=batch.id, type='out',
                quantity=take, operation_id=draft.operation_id,
                doctor_name=draft.doctor_name,
                note=f'Подтверждено врачом: {draft.note or ""}'
            ))
        draft.status = 'confirmed'
        draft.confirmed_by = current_user.id
        draft.confirmed_at = datetime.utcnow()
        db.session.commit()
        flash(f'Списание подтверждено: {material.name} — {draft.quantity} шт.', 'success')
        return redirect(url_for('spending_confirm_list'))

    @app.route('/spending/<int:id>/reject', methods=['POST'])
    @login_required
    def spending_reject(id):
        if not current_user.can_confirm():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))
        draft = SpendingDraft.query.get_or_404(id)
        draft.status = 'rejected'
        draft.confirmed_by = current_user.id
        draft.confirmed_at = datetime.utcnow()
        db.session.commit()
        flash('Списание отклонено.', 'info')
        return redirect(url_for('spending_confirm_list'))

    # ──────────────────────────────────────────
    # ЗАЯВКИ НА ПЕРЕМЕЩЕНИЕ
    # ──────────────────────────────────────────
    @app.route('/requests')
    @login_required
    def requests_list():
        if current_user.can_create_request():
            reqs = TransferRequest.query.order_by(TransferRequest.created_at.desc()).all()
        else:
            reqs = TransferRequest.query.filter_by(from_user_id=current_user.id).order_by(TransferRequest.created_at.desc()).all()
        return render_template('requests.html', requests=reqs)

    @app.route('/requests/add', methods=['GET', 'POST'])
    @login_required
    def request_add():
        if not current_user.can_create_request():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))
        if request.method == 'POST':
            mat_id = request.form.get('material_id')
            qty = int(request.form.get('quantity', 0))
            note = request.form.get('note', '').strip()
            if not mat_id or qty <= 0:
                flash('Укажите материал и количество.', 'danger')
                return redirect(url_for('request_add'))
            req = TransferRequest(
                from_user_id=current_user.id, material_id=int(mat_id),
                quantity=qty, note=note, to_location_code='oper'
            )
            db.session.add(req)
            db.session.commit()
            flash('Заявка создана!', 'success')
            return redirect(url_for('requests_list'))
        materials_list = Material.query.order_by(Material.name).all()
        return render_template('request_form.html', materials=materials_list)

    # ──────────────────────────────────────────
    # ЖУРНАЛ ОПЕРАЦИЙ
    # ──────────────────────────────────────────
    @app.route('/transactions')
    @login_required
    def transactions():
        transactions_list = Transaction.query.order_by(Transaction.created_at.desc()).limit(100).all()
        return render_template('transactions.html', transactions=transactions_list)

    return app


if __name__ == '__main__':
    app = create_app()
    port = int(os.environ.get('PORT', 5050))
    app.run(debug=False, host='0.0.0.0', port=port)